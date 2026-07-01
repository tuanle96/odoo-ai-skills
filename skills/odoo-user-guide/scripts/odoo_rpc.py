#!/usr/bin/env python3
"""Tiny Odoo external-API client (stdlib xmlrpc.client only — no dependency).

Used by `odoo-guide-run` to create the owned test record, assert the final
state at the backend (the proof), and clean up afterwards. Kept separate from
the pure `odoo_guide_lib` because it does I/O.
"""
from __future__ import annotations

import xmlrpc.client


def _recipe_sale_order(rpc) -> int:
    """Convenience recipe: a throwaway draft sale.order (partner + service line)."""
    partner = rpc.execute("res.partner", "search", [["customer_rank", ">", 0]], limit=1) \
        or rpc.execute("res.partner", "search", [], limit=1)
    product = rpc.execute("product.product", "search", [["sale_ok", "=", True], ["type", "=", "service"]], limit=1) \
        or rpc.execute("product.product", "search", [["sale_ok", "=", True]], limit=1)
    if not partner or not product:
        raise RuntimeError("no partner/service product available to build a test sale.order")
    return rpc.create("sale.order", {"partner_id": partner[0],
                                     "order_line": [(0, 0, {"product_id": product[0], "product_uom_qty": 1})]})


# Optional auto-create recipes. Model-agnostic guides use --record-id instead; add a
# builder here only for common demo flows. Keeping this the ONE place tied to a model.
RECIPES = {"sale.order": _recipe_sale_order}


def obtain_record(rpc, model: str, record_id: int | None = None) -> tuple[int, bool]:
    """Return (record_id, created_by_us). Generic across models: pass an existing
    record_id (nothing created or torn down), or auto-create via a known recipe.
    Raises RuntimeError (with guidance) when neither applies — callers turn that into
    a clean exit."""
    if record_id:
        return record_id, False
    recipe = RECIPES.get(model)
    if recipe:
        return recipe(rpc), True
    raise RuntimeError(f"no built-in test-record recipe for {model}. Pass --record-id <id> to document "
                       f"an existing record, or drive a CREATE flow with the agent (New → fill → save).")


class OdooRPC:
    def __init__(self, url: str, db: str, login: str, password: str):
        self.url, self.db, self.password = url.rstrip("/"), db, password
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = common.authenticate(db, login, password, {})
        if not self.uid:
            raise SystemExit(f"❌ Odoo auth failed for {login} on {db}")
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")

    def execute(self, model: str, method: str, *args, **kw):
        return self.models.execute_kw(self.db, self.uid, self.password, model, method, list(args), kw)

    def create(self, model: str, vals: dict, context: dict | None = None) -> int:
        ctx = {"tracking_disable": True, "mail_create_nosubscribe": True, "mail_notrack": True}
        ctx.update(context or {})
        return self.execute(model, "create", vals, context=ctx)

    def get_view_arch(self, model: str, view_type: str = "form") -> str:
        """Form-view arch XML over the external API — works on any deployment."""
        view = self.execute(model, "get_view", view_type=view_type)
        return view.get("arch", "")

    def access_rights(self, model: str) -> dict:
        """Effective {read,write,create,unlink} for the authenticated user."""
        return {op: bool(self.execute(model, "check_access_rights", op, raise_exception=False))
                for op in ("read", "write", "create", "unlink")}

    def read_field(self, model: str, rec_id: int, field: str):
        rows = self.execute(model, "read", [rec_id], fields=[field])
        return rows[0][field] if rows else None

    def cleanup_record(self, model: str, rec_id: int) -> None:
        """Best-effort, MODEL-AGNOSTIC teardown of a record the guide run created,
        so nothing lingers live. Tries, in order and each guarded: unlock (locked
        orders), cancel (incl. the `*.cancel` confirmation wizard some models pop),
        `button_cancel` (purchase-style), then delete; archive only as a last
        resort. Works for sale.order, purchase.order, and others without any
        per-model special-casing.
        """
        for method in ("action_unlock", "action_draft"):
            try:
                self.execute(model, method, [rec_id])
            except Exception:
                pass
        for cancel in ("action_cancel", "button_cancel"):
            try:
                res = self.execute(model, cancel, [rec_id])
                if isinstance(res, dict) and str(res.get("res_model", "")).endswith(".cancel"):
                    wiz_model = res["res_model"]
                    wiz = self.execute(wiz_model, "create", {"order_id": rec_id})
                    for act in ("action_cancel", "cancel"):
                        try:
                            self.execute(wiz_model, act, [wiz]); break
                        except Exception:
                            continue
                break
            except Exception:
                continue
        try:
            self.execute(model, "unlink", [rec_id])  # prefer deleting the throwaway record
            return
        except Exception:
            pass
        self.archive(model, rec_id)

    def archive(self, model: str, rec_id: int) -> None:
        try:
            self.execute(model, "action_archive", [rec_id])
        except Exception:
            try:
                self.execute(model, "write", [rec_id], {"active": False})
            except Exception:
                pass

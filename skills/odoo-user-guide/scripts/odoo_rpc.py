#!/usr/bin/env python3
"""Tiny Odoo external-API client (stdlib xmlrpc.client only — no dependency).

Used by `odoo-guide-run` to create the owned test record, assert the final
state at the backend (the proof), and clean up afterwards. Kept separate from
the pure `odoo_guide_lib` because it does I/O.
"""
from __future__ import annotations

import xmlrpc.client


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
        """Undo a guide run's owned test record so nothing lingers live.

        sale.order needs its own teardown: a confirmed order may be LOCKED and
        cancelling pops the `sale.order.cancel` confirmation wizard — a plain
        `action_cancel` over RPC silently no-ops. Other models fall back to
        archiving.
        """
        if model == "sale.order":
            try:
                self.execute(model, "action_unlock", [rec_id])
            except Exception:
                pass
            try:
                res = self.execute(model, "action_cancel", [rec_id])
                if isinstance(res, dict) and res.get("res_model") == "sale.order.cancel":
                    wiz = self.execute("sale.order.cancel", "create", {"order_id": rec_id})
                    self.execute("sale.order.cancel", "action_cancel", [wiz])
            except Exception:
                pass
        else:
            self.archive(model, rec_id)

    def archive(self, model: str, rec_id: int) -> None:
        try:
            self.execute(model, "action_archive", [rec_id])
        except Exception:
            try:
                self.execute(model, "write", [rec_id], {"active": False})
            except Exception:
                pass

# Worked example for the odoo-ai-skills suite — see examples/sale-order-walkthrough.md
{
    "name": "Sale Confirm Guard (example)",
    "summary": "Require a delivery date before a quotation can be confirmed.",
    "version": "18.0.1.0.0",
    "license": "LGPL-3",
    "category": "Sales",
    # Layer A (model_brief) showed `action_confirm` is owned by `sale`
    # (bottom of the MRO, has_super=False) and that commitment_date is a
    # `sale` field — so depending on `sale` is both necessary and sufficient
    # for this override to resolve ABOVE the base implementation.
    "depends": ["sale"],
    "installable": True,
}

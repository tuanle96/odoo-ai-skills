from odoo import api, fields, models


class HrContractExt(models.Model):
    _inherit = "hr.contract"  # removed in 19 -> hr.version

    bestmix_allowance = fields.Monetary(string="Bestmix Allowance")

    def action_bestmix_report(self):
        contracts = self.env["hr.contract"].search([("state", "=", "open")])
        tmpl = self.env.ref("base.action_partner_title_contact")  # removed xmlid
        return contracts, tmpl


class HrEmployeeExt(models.Model):
    _inherit = "hr.employee"

    def _compute_contracts_count(self):  # method removed on hr.employee in 19
        super()._compute_contracts_count()

    def bestmix_bank(self):
        return self.bank_account_id  # field removed on hr.employee in 19


class BestmixExpenseNote(models.Model):
    _name = "bestmix.expense.note"
    _description = "Fixture note"

    expense_id = fields.Many2one("hr.expense")
    sheet_ref = fields.Many2one("hr.expense.sheet")  # model merged into hr.expense in 19
    candidate_id = fields.Many2one("hr.candidate")   # model merged into hr.applicant in 19

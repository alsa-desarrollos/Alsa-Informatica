from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AlsaCustomerCreditAdjustWizard(models.TransientModel):
    _name = "alsa.customer.credit.adjust.wizard"
    _description = "Ajustar saldo de cliente"

    target_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Cliente",
        required=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Compañía",
        required=True,
        readonly=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        related="company_id.currency_id",
        readonly=True,
    )
    balance_kind = fields.Selection(
        selection=[
            ("credit", "Saldo a favor"),
            ("discount", "Saldo de descuento"),
        ],
        string="Clase de saldo",
        required=True,
        readonly=True,
        default="credit",
    )
    current_balance = fields.Monetary(
        string="Saldo actual",
        compute="_compute_current_balance",
        currency_field="currency_id",
        readonly=True,
    )
    new_balance = fields.Monetary(
        string="Nuevo saldo",
        required=True,
        currency_field="currency_id",
    )
    reference = fields.Char(string="Referencia")
    reason = fields.Text(string="Motivo", required=True)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        partner_id = values.get("target_partner_id")
        company_id = values.get("company_id") or self.env.company.id
        balance_kind = values.get("balance_kind") or "credit"
        if partner_id and "new_balance" in fields_list:
            values["new_balance"] = self.env[
                "alsa.customer.credit.movement"
            ]._get_partner_balance(
                self.env["res.partner"].browse(partner_id),
                self.env["res.company"].browse(company_id),
                balance_kind,
            )
        return values

    @api.depends("target_partner_id", "company_id", "balance_kind")
    def _compute_current_balance(self):
        Movement = self.env["alsa.customer.credit.movement"]
        for wizard in self:
            wizard.current_balance = (
                Movement._get_partner_balance(
                    wizard.target_partner_id,
                    wizard.company_id,
                    wizard.balance_kind,
                )
                if wizard.target_partner_id and wizard.company_id
                else 0.0
            )

    @api.onchange("target_partner_id", "company_id", "balance_kind")
    def _onchange_credit_target(self):
        self.new_balance = self.current_balance

    def action_apply(self):
        self.ensure_one()
        if not self.reason or not self.reason.strip():
            raise ValidationError(_("Debe indicar el motivo del ajuste."))
        movement = self.env[
            "alsa.customer.credit.movement"
        ]._create_balance_adjustment(
            partner=self.target_partner_id,
            company=self.company_id,
            balance_kind=self.balance_kind,
            new_balance=self.new_balance,
            reason=self.reason,
            reference=self.reference,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Saldo actualizado"),
                "message": _(
                    "El %(kind)s de %(partner)s ahora es %(balance).2f %(currency)s.",
                    kind=(
                        _("saldo a favor")
                        if movement.balance_kind == "credit"
                        else _("saldo de descuento")
                    ),
                    partner=movement.partner_id.display_name,
                    balance=movement.balance_after,
                    currency=movement.currency_id.name,
                ),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class AlsaCustomerCreditMovement(models.Model):
    _name = "alsa.customer.credit.movement"
    _description = "Movimiento de saldo a favor"
    _order = "movement_datetime desc, id desc"
    _rec_name = "name"

    name = fields.Char(
        string="Movimiento",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("Nuevo"),
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Propietario del saldo",
        required=True,
        index=True,
        ondelete="restrict",
        readonly=True,
    )
    commercial_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Empresa comercial",
        related="partner_id.commercial_partner_id",
        store=True,
        index=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Compañía",
        required=True,
        index=True,
        readonly=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Moneda",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    movement_datetime = fields.Datetime(
        string="Fecha y hora",
        required=True,
        readonly=True,
        default=fields.Datetime.now,
    )
    movement_type = fields.Selection(
        selection=[
            ("increase", "Aumento"),
            ("decrease", "Disminución"),
        ],
        string="Tipo",
        required=True,
        readonly=True,
    )
    amount = fields.Monetary(
        string="Movimiento",
        required=True,
        readonly=True,
        currency_field="currency_id",
        help="Un importe positivo aumenta el saldo y uno negativo lo disminuye.",
    )
    balance_before = fields.Monetary(
        string="Saldo anterior",
        required=True,
        readonly=True,
        currency_field="currency_id",
    )
    balance_after = fields.Monetary(
        string="Saldo posterior",
        required=True,
        readonly=True,
        currency_field="currency_id",
    )
    reference = fields.Char(string="Referencia", readonly=True)
    reason = fields.Text(string="Motivo", required=True, readonly=True)
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Registrado por",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
    )

    @api.model
    def _get_partner_balance(self, partner, company):
        movements = self.search(
            [
                ("partner_id", "=", partner.id),
                ("company_id", "=", company.id),
            ]
        )
        return sum(movements.mapped("amount"))

    @api.model
    def _create_balance_adjustment(
        self, partner, company, new_balance, reason, reference=None
    ):
        if not self.env.user.has_group(
            "alsa_customer_credit.group_alsa_customer_credit_manager"
        ):
            raise AccessError(_("No tiene permiso para modificar saldos a favor."))

        partner = partner.exists()
        company = company.exists()
        if not partner or not company:
            raise ValidationError(_("El cliente y la compañía son obligatorios."))
        if not reason or not reason.strip():
            raise ValidationError(_("Debe indicar el motivo del ajuste."))
        if company not in self.env.companies:
            raise AccessError(_("No tiene acceso a la compañía seleccionada."))

        currency = company.currency_id
        new_balance = currency.round(new_balance)
        if currency.compare_amounts(new_balance, 0.0) < 0:
            raise ValidationError(_("El saldo a favor no puede ser negativo."))

        # Todos los ajustes pasan por este bloqueo para evitar que dos usuarios
        # calculen simultáneamente la diferencia desde el mismo saldo anterior.
        self.env.cr.execute(
            "SELECT id FROM res_partner WHERE id = %s FOR UPDATE",
            [partner.id],
        )
        balance_before = currency.round(
            self._get_partner_balance(partner, company)
        )
        difference = currency.round(new_balance - balance_before)
        if currency.is_zero(difference):
            raise UserError(_("El nuevo saldo es igual al saldo actual."))

        movement = self.with_context(alsa_credit_internal_create=True).create(
            {
                "partner_id": partner.id,
                "company_id": company.id,
                "movement_type": "increase" if difference > 0 else "decrease",
                "amount": difference,
                "balance_before": balance_before,
                "balance_after": new_balance,
                "reference": reference,
                "reason": reason.strip(),
                "user_id": self.env.user.id,
            }
        )
        partner.message_post(
            body=_(
                "Saldo a favor ajustado de %(before).2f %(currency)s a "
                "%(after).2f %(currency)s. Motivo: %(reason)s",
                before=balance_before,
                after=new_balance,
                currency=currency.name,
                reason=reason.strip(),
            )
        )
        return movement

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get("alsa_credit_internal_create"):
            raise AccessError(
                _("Los movimientos solo pueden generarse mediante Ajustar saldo.")
            )
        for values in vals_list:
            if not values.get("reason", "").strip():
                raise ValidationError(_("Debe indicar el motivo del ajuste."))
            if values.get("name", _("Nuevo")) == _("Nuevo"):
                values["name"] = (
                    self.env["ir.sequence"].next_by_code(
                        "alsa.customer.credit.movement"
                    )
                    or _("Nuevo")
                )
        return super().create(vals_list)

    def write(self, values):
        raise AccessError(
            _("Los movimientos de saldo a favor no pueden modificarse.")
        )

    def unlink(self):
        raise AccessError(
            _("Los movimientos de saldo a favor no pueden eliminarse.")
        )


class ResPartner(models.Model):
    _inherit = "res.partner"

    alsa_credit_currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Moneda del saldo",
        compute="_compute_alsa_credit_balances",
    )
    alsa_credit_contact_balance = fields.Monetary(
        string="Saldo del contacto",
        compute="_compute_alsa_credit_balances",
        currency_field="alsa_credit_currency_id",
    )
    alsa_credit_company_balance = fields.Monetary(
        string="Saldo de la empresa",
        compute="_compute_alsa_credit_balances",
        currency_field="alsa_credit_currency_id",
    )
    alsa_credit_total_available = fields.Monetary(
        string="Saldo a favor disponible",
        compute="_compute_alsa_credit_balances",
        currency_field="alsa_credit_currency_id",
    )

    @api.depends_context("company")
    def _compute_alsa_credit_balances(self):
        company = self.env.company
        partner_ids = set(self.ids)
        partner_ids.update(self.mapped("commercial_partner_id").ids)
        movements = self.env["alsa.customer.credit.movement"].search(
            [
                ("partner_id", "in", list(partner_ids)),
                ("company_id", "=", company.id),
            ]
        )
        totals = {}
        for movement in movements:
            totals[movement.partner_id.id] = (
                totals.get(movement.partner_id.id, 0.0) + movement.amount
            )

        for partner in self:
            exact_balance = totals.get(partner.id, 0.0)
            commercial_partner = partner.commercial_partner_id
            if partner.parent_id:
                contact_balance = exact_balance
                company_balance = totals.get(commercial_partner.id, 0.0)
            elif partner.is_company:
                contact_balance = 0.0
                company_balance = exact_balance
            else:
                contact_balance = exact_balance
                company_balance = 0.0

            partner.alsa_credit_currency_id = company.currency_id
            partner.alsa_credit_contact_balance = contact_balance
            partner.alsa_credit_company_balance = company_balance
            partner.alsa_credit_total_available = (
                contact_balance + company_balance
            )

    def _alsa_credit_movement_partner_ids(self):
        self.ensure_one()
        partners = self
        if self.parent_id:
            partners |= self.commercial_partner_id
        return partners.ids

    def action_view_alsa_credit_movements(self):
        self.ensure_one()
        company = self.env.company
        return {
            "type": "ir.actions.act_window",
            "name": _("Movimientos de saldo a favor"),
            "res_model": "alsa.customer.credit.movement",
            "view_mode": "list,form",
            "domain": [
                ("partner_id", "in", self._alsa_credit_movement_partner_ids()),
                ("company_id", "=", company.id),
            ],
            "context": {"create": False, "delete": False},
        }

    def _alsa_action_adjust_credit(self, target_partner):
        self.ensure_one()
        if not self.env.user.has_group(
            "alsa_customer_credit.group_alsa_customer_credit_manager"
        ):
            raise AccessError(_("No tiene permiso para modificar saldos a favor."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Ajustar saldo a favor"),
            "res_model": "alsa.customer.credit.adjust.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_target_partner_id": target_partner.id,
                "default_company_id": self.env.company.id,
            },
        }

    def action_adjust_alsa_contact_credit(self):
        self.ensure_one()
        if self.is_company:
            raise UserError(_("Las empresas no tienen un saldo de contacto."))
        return self._alsa_action_adjust_credit(self)

    def action_adjust_alsa_company_credit(self):
        self.ensure_one()
        target_partner = self.commercial_partner_id
        if not target_partner.is_company:
            raise UserError(_("Este contacto no pertenece a una empresa."))
        return self._alsa_action_adjust_credit(target_partner)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    alsa_credit_currency_id = fields.Many2one(
        comodel_name="res.currency",
        compute="_compute_alsa_credit_balances",
    )
    alsa_credit_contact_balance = fields.Monetary(
        string="Saldo del contacto",
        compute="_compute_alsa_credit_balances",
        currency_field="alsa_credit_currency_id",
    )
    alsa_credit_company_balance = fields.Monetary(
        string="Saldo de la empresa",
        compute="_compute_alsa_credit_balances",
        currency_field="alsa_credit_currency_id",
    )
    alsa_credit_total_available = fields.Monetary(
        string="Saldo a favor disponible",
        compute="_compute_alsa_credit_balances",
        currency_field="alsa_credit_currency_id",
    )

    @api.depends("partner_id", "company_id")
    def _compute_alsa_credit_balances(self):
        for order in self:
            company = order.company_id or self.env.company
            partner = order.partner_id.with_company(company)
            order.alsa_credit_currency_id = company.currency_id
            order.alsa_credit_contact_balance = (
                partner.alsa_credit_contact_balance if partner else 0.0
            )
            order.alsa_credit_company_balance = (
                partner.alsa_credit_company_balance if partner else 0.0
            )
            order.alsa_credit_total_available = (
                partner.alsa_credit_total_available if partner else 0.0
            )

    def action_view_alsa_credit_movements(self):
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Seleccione primero un cliente."))
        company = self.company_id or self.env.company
        return self.partner_id.with_company(
            company
        ).action_view_alsa_credit_movements()


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    alsa_credit_currency_id = fields.Many2one(
        comodel_name="res.currency",
        compute="_compute_alsa_credit_balances",
    )
    alsa_credit_contact_balance = fields.Monetary(
        string="Saldo del contacto",
        compute="_compute_alsa_credit_balances",
        currency_field="alsa_credit_currency_id",
    )
    alsa_credit_company_balance = fields.Monetary(
        string="Saldo de la empresa",
        compute="_compute_alsa_credit_balances",
        currency_field="alsa_credit_currency_id",
    )
    alsa_credit_total_available = fields.Monetary(
        string="Saldo a favor disponible",
        compute="_compute_alsa_credit_balances",
        currency_field="alsa_credit_currency_id",
    )

    @api.depends("partner_id", "company_id")
    def _compute_alsa_credit_balances(self):
        for ticket in self:
            company = ticket.company_id or self.env.company
            partner = ticket.partner_id.with_company(company)
            ticket.alsa_credit_currency_id = company.currency_id
            ticket.alsa_credit_contact_balance = (
                partner.alsa_credit_contact_balance if partner else 0.0
            )
            ticket.alsa_credit_company_balance = (
                partner.alsa_credit_company_balance if partner else 0.0
            )
            ticket.alsa_credit_total_available = (
                partner.alsa_credit_total_available if partner else 0.0
            )

    def action_view_alsa_credit_movements(self):
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Seleccione primero un cliente."))
        company = self.company_id or self.env.company
        return self.partner_id.with_company(
            company
        ).action_view_alsa_credit_movements()

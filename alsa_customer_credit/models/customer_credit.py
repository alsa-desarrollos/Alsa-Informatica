from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class AlsaCustomerCreditMovement(models.Model):
    _name = "alsa.customer.credit.movement"
    _description = "Movimiento de saldo de cliente"
    _order = "movement_datetime desc, id desc"
    _rec_name = "name"

    name = fields.Char(
        string="Folio",
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
    balance_kind = fields.Selection(
        selection=[
            ("credit", "Saldo a favor"),
            ("discount", "Saldo de descuento"),
        ],
        string="Clase de saldo",
        required=True,
        default="credit",
        index=True,
        help=(
            "El saldo a favor representa dinero o anticipos del cliente. "
            "El saldo de descuento representa un beneficio comercial no reembolsable."
        ),
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
        string="Importe del movimiento",
        required=True,
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
    reference = fields.Char(string="Referencia")
    reason = fields.Text(string="Motivo", required=True)
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Registrado por",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
    )

    @api.model
    def _get_partner_balance(self, partner, company, balance_kind="credit"):
        movements = self.search(
            [
                ("partner_id", "=", partner.id),
                ("company_id", "=", company.id),
                ("balance_kind", "=", balance_kind),
            ]
        )
        return sum(movements.mapped("amount"))

    @api.model
    def _create_balance_adjustment(
        self,
        partner,
        company,
        balance_kind,
        new_balance,
        reason,
        reference=None,
    ):
        if not self.env.user.has_group(
            "alsa_customer_credit.group_alsa_customer_credit_manager"
        ):
            raise AccessError(_("No tiene permiso para modificar saldos de clientes."))

        partner = partner.exists()
        company = company.exists()
        if not partner or not company:
            raise ValidationError(_("El cliente y la compañía son obligatorios."))
        if not reason or not reason.strip():
            raise ValidationError(_("Debe indicar el motivo del ajuste."))
        if company not in self.env.companies:
            raise AccessError(_("No tiene acceso a la compañía seleccionada."))
        if balance_kind not in {"credit", "discount"}:
            raise ValidationError(_("La clase de saldo no es válida."))

        currency = company.currency_id
        new_balance = currency.round(new_balance)
        if currency.compare_amounts(new_balance, 0.0) < 0:
            raise ValidationError(_("El saldo del cliente no puede ser negativo."))

        # Todos los ajustes pasan por este bloqueo para evitar que dos usuarios
        # calculen simultáneamente la diferencia desde el mismo saldo anterior.
        self.env.cr.execute(
            "SELECT id FROM res_partner WHERE id = %s FOR UPDATE",
            [partner.id],
        )
        balance_before = currency.round(
            self._get_partner_balance(partner, company, balance_kind)
        )
        difference = currency.round(new_balance - balance_before)
        if currency.is_zero(difference):
            raise UserError(_("El nuevo saldo es igual al saldo actual."))

        movement = self.with_context(alsa_credit_internal_create=True).create(
            {
                "partner_id": partner.id,
                "company_id": company.id,
                "balance_kind": balance_kind,
                "movement_type": "increase" if difference > 0 else "decrease",
                "amount": difference,
                "balance_before": balance_before,
                "balance_after": new_balance,
                "reference": reference,
                "reason": reason.strip(),
                "user_id": self.env.user.id,
            }
        )
        balance_label = (
            _("Saldo a favor")
            if balance_kind == "credit"
            else _("Saldo de descuento")
        )
        partner.message_post(
            body=_(
                "%(kind)s ajustado de %(before).2f %(currency)s a "
                "%(after).2f %(currency)s. Motivo: %(reason)s",
                kind=balance_label,
                before=balance_before,
                after=new_balance,
                currency=currency.name,
                reason=reason.strip(),
            )
        )
        return movement

    @api.model_create_multi
    def create(self, vals_list):
        internal_create = self.env.context.get("alsa_credit_internal_create")
        manual_create = self.env.context.get("alsa_credit_manual_create")
        if not internal_create and not manual_create:
            raise AccessError(
                _("Los movimientos solo pueden generarse desde la pestaña Saldos.")
            )
        if manual_create:
            if not self.env.user.has_group(
                "alsa_customer_credit.group_alsa_customer_credit_manager"
            ):
                raise AccessError(
                    _("No tiene permiso para registrar movimientos de saldo.")
                )
            movements = self.browse()
            for values in vals_list:
                partner = self.env["res.partner"].browse(
                    values.get("partner_id")
                ).exists()
                company = self.env["res.company"].browse(
                    values.get("company_id") or self.env.company.id
                ).exists()
                balance_kind = values.get("balance_kind") or "credit"
                reason = (values.get("reason") or "").strip()
                if not partner or not company:
                    raise ValidationError(
                        _("El cliente y la compañía son obligatorios.")
                    )
                if company not in self.env.companies:
                    raise AccessError(
                        _("No tiene acceso a la compañía seleccionada.")
                    )
                if balance_kind not in {"credit", "discount"}:
                    raise ValidationError(_("La clase de saldo no es válida."))
                if not reason:
                    raise ValidationError(_("Debe indicar el motivo del movimiento."))

                currency = company.currency_id
                amount = currency.round(values.get("amount") or 0.0)
                if currency.is_zero(amount):
                    raise ValidationError(
                        _("El importe del movimiento no puede ser cero.")
                    )

                self.env.cr.execute(
                    "SELECT id FROM res_partner WHERE id = %s FOR UPDATE",
                    [partner.id],
                )
                balance_before = currency.round(
                    self._get_partner_balance(partner, company, balance_kind)
                )
                balance_after = currency.round(balance_before + amount)
                if currency.compare_amounts(balance_after, 0.0) < 0:
                    raise ValidationError(
                        _("El movimiento no puede dejar el saldo por debajo de cero.")
                    )

                values.update(
                    {
                        "partner_id": partner.id,
                        "company_id": company.id,
                        "balance_kind": balance_kind,
                        "movement_type": "increase" if amount > 0 else "decrease",
                        "amount": amount,
                        "balance_before": balance_before,
                        "balance_after": balance_after,
                        "reason": reason,
                        "user_id": self.env.user.id,
                    }
                )
                if values.get("name", _("Nuevo")) == _("Nuevo"):
                    sequence_code = (
                        "alsa.customer.discount.movement"
                        if balance_kind == "discount"
                        else "alsa.customer.credit.movement"
                    )
                    values["name"] = (
                        self.env["ir.sequence"].next_by_code(sequence_code)
                        or _("Nuevo")
                    )
                movement = super().create([values])
                movements |= movement
                movement.partner_id.message_post(
                    body=_(
                        "%(kind)s: movimiento de %(amount).2f %(currency)s. "
                        "Saldo resultante: %(balance).2f %(currency)s. Motivo: %(reason)s",
                        kind=(
                            _("Saldo a favor")
                            if balance_kind == "credit"
                            else _("Saldo de descuento")
                        ),
                        amount=amount,
                        balance=balance_after,
                        currency=currency.name,
                        reason=reason,
                    )
                )
            return movements

        for values in vals_list:
            if not values.get("reason", "").strip():
                raise ValidationError(_("Debe indicar el motivo del ajuste."))
            if values.get("name", _("Nuevo")) == _("Nuevo"):
                sequence_code = (
                    "alsa.customer.discount.movement"
                    if values.get("balance_kind") == "discount"
                    else "alsa.customer.credit.movement"
                )
                values["name"] = (
                    self.env["ir.sequence"].next_by_code(sequence_code)
                    or _("Nuevo")
                )
        return super().create(vals_list)

    def write(self, values):
        raise AccessError(
            _("Los movimientos de saldos de clientes no pueden modificarse.")
        )

    def unlink(self):
        raise AccessError(
            _("Los movimientos de saldos de clientes no pueden eliminarse.")
        )


class ResPartner(models.Model):
    _inherit = "res.partner"

    alsa_balance_movement_ids = fields.One2many(
        comodel_name="alsa.customer.credit.movement",
        inverse_name="partner_id",
        string="Movimientos de saldo",
    )

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
        inverse="_inverse_alsa_credit_total_available",
        currency_field="alsa_credit_currency_id",
    )
    alsa_discount_contact_balance = fields.Monetary(
        string="Descuento del contacto",
        compute="_compute_alsa_credit_balances",
        currency_field="alsa_credit_currency_id",
    )
    alsa_discount_company_balance = fields.Monetary(
        string="Descuento de la empresa",
        compute="_compute_alsa_credit_balances",
        currency_field="alsa_credit_currency_id",
    )
    alsa_discount_total_available = fields.Monetary(
        string="Saldo de descuento disponible",
        compute="_compute_alsa_credit_balances",
        inverse="_inverse_alsa_discount_total_available",
        currency_field="alsa_credit_currency_id",
    )

    def _inverse_alsa_balance_total(self, balance_kind):
        Movement = self.env["alsa.customer.credit.movement"]
        company = self.env.company
        for partner in self:
            desired_total = (
                partner.alsa_credit_total_available
                if balance_kind == "credit"
                else partner.alsa_discount_total_available
            )
            target_partner = partner
            target_balance = desired_total
            if partner.parent_id:
                company_balance = Movement._get_partner_balance(
                    partner.commercial_partner_id,
                    company,
                    balance_kind,
                )
                target_balance -= company_balance

            currency = company.currency_id
            target_balance = currency.round(target_balance)
            if currency.compare_amounts(target_balance, 0.0) < 0:
                raise ValidationError(
                    _(
                        "El total indicado es menor que el saldo disponible de la "
                        "empresa. Registre el movimiento desde la empresa."
                    )
                )
            current_balance = Movement._get_partner_balance(
                target_partner,
                company,
                balance_kind,
            )
            if currency.is_zero(target_balance - current_balance):
                continue
            Movement._create_balance_adjustment(
                partner=target_partner,
                company=company,
                balance_kind=balance_kind,
                new_balance=target_balance,
                reason=_("Ajuste directo desde la pestaña Saldos"),
                reference=_("Formulario del contacto"),
            )

    def _inverse_alsa_credit_total_available(self):
        self._inverse_alsa_balance_total("credit")

    def _inverse_alsa_discount_total_available(self):
        self._inverse_alsa_balance_total("discount")

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
            total_key = (movement.partner_id.id, movement.balance_kind)
            totals[total_key] = (
                totals.get(total_key, 0.0) + movement.amount
            )

        for partner in self:
            commercial_partner = partner.commercial_partner_id
            balance_values = {}
            for balance_kind in ("credit", "discount"):
                exact_balance = totals.get((partner.id, balance_kind), 0.0)
                if partner.parent_id:
                    contact_balance = exact_balance
                    company_balance = totals.get(
                        (commercial_partner.id, balance_kind), 0.0
                    )
                elif partner.is_company:
                    contact_balance = 0.0
                    company_balance = exact_balance
                else:
                    contact_balance = exact_balance
                    company_balance = 0.0
                balance_values[balance_kind] = (
                    contact_balance,
                    company_balance,
                    contact_balance + company_balance,
                )

            partner.alsa_credit_currency_id = company.currency_id
            (
                partner.alsa_credit_contact_balance,
                partner.alsa_credit_company_balance,
                partner.alsa_credit_total_available,
            ) = balance_values["credit"]
            (
                partner.alsa_discount_contact_balance,
                partner.alsa_discount_company_balance,
                partner.alsa_discount_total_available,
            ) = balance_values["discount"]

    def _alsa_credit_movement_partner_ids(self):
        self.ensure_one()
        partners = self
        if self.parent_id:
            partners |= self.commercial_partner_id
        return partners.ids

    def _alsa_action_view_balance_movements(self, balance_kind):
        self.ensure_one()
        company = self.env.company
        balance_label = (
            _("saldo a favor")
            if balance_kind == "credit"
            else _("saldo de descuento")
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Movimientos de %(kind)s", kind=balance_label),
            "res_model": "alsa.customer.credit.movement",
            "view_mode": "list,form",
            "domain": [
                ("partner_id", "in", self._alsa_credit_movement_partner_ids()),
                ("company_id", "=", company.id),
                ("balance_kind", "=", balance_kind),
            ],
            "context": {"create": False, "delete": False},
        }

    def action_view_alsa_credit_movements(self):
        return self._alsa_action_view_balance_movements("credit")

    def action_view_alsa_discount_movements(self):
        return self._alsa_action_view_balance_movements("discount")

    def _alsa_action_adjust_credit(self, target_partner, balance_kind):
        self.ensure_one()
        if not self.env.user.has_group(
            "alsa_customer_credit.group_alsa_customer_credit_manager"
        ):
            raise AccessError(_("No tiene permiso para modificar saldos de clientes."))
        balance_label = (
            _("saldo a favor")
            if balance_kind == "credit"
            else _("saldo de descuento")
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Ajustar %(kind)s", kind=balance_label),
            "res_model": "alsa.customer.credit.adjust.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_target_partner_id": target_partner.id,
                "default_company_id": self.env.company.id,
                "default_balance_kind": balance_kind,
            },
        }

    def action_adjust_alsa_contact_credit(self):
        self.ensure_one()
        if self.is_company:
            raise UserError(_("Las empresas no tienen un saldo de contacto."))
        return self._alsa_action_adjust_credit(self, "credit")

    def action_adjust_alsa_company_credit(self):
        self.ensure_one()
        target_partner = self.commercial_partner_id
        if not target_partner.is_company:
            raise UserError(_("Este contacto no pertenece a una empresa."))
        return self._alsa_action_adjust_credit(target_partner, "credit")

    def action_adjust_alsa_contact_discount(self):
        self.ensure_one()
        if self.is_company:
            raise UserError(_("Las empresas no tienen un saldo de contacto."))
        return self._alsa_action_adjust_credit(self, "discount")

    def action_adjust_alsa_company_discount(self):
        self.ensure_one()
        target_partner = self.commercial_partner_id
        if not target_partner.is_company:
            raise UserError(_("Este contacto no pertenece a una empresa."))
        return self._alsa_action_adjust_credit(target_partner, "discount")


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
    alsa_discount_total_available = fields.Monetary(
        string="Saldo de descuento disponible",
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
            order.alsa_discount_total_available = (
                partner.alsa_discount_total_available if partner else 0.0
            )

    def action_view_alsa_credit_movements(self):
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Seleccione primero un cliente."))
        company = self.company_id or self.env.company
        return self.partner_id.with_company(
            company
        ).action_view_alsa_credit_movements()

    def action_view_alsa_discount_movements(self):
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Seleccione primero un cliente."))
        company = self.company_id or self.env.company
        return self.partner_id.with_company(
            company
        ).action_view_alsa_discount_movements()


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
    alsa_discount_total_available = fields.Monetary(
        string="Saldo de descuento disponible",
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
            ticket.alsa_discount_total_available = (
                partner.alsa_discount_total_available if partner else 0.0
            )

    def action_view_alsa_credit_movements(self):
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Seleccione primero un cliente."))
        company = self.company_id or self.env.company
        return self.partner_id.with_company(
            company
        ).action_view_alsa_credit_movements()

    def action_view_alsa_discount_movements(self):
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_("Seleccione primero un cliente."))
        company = self.company_id or self.env.company
        return self.partner_id.with_company(
            company
        ).action_view_alsa_discount_movements()

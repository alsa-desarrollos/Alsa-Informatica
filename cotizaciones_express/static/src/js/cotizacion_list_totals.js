/** @odoo-module **/

import { formatMonetary } from "@web/views/fields/formatters";
import { ListRenderer } from "@web/views/list/list_renderer";
import { listView } from "@web/views/list/list_view";
import { registry } from "@web/core/registry";
import { onWillRender, onWillStart, useState } from "@odoo/owl";

const TOTAL_FIELDS = ["amount_total", "amount_confirmed"];

/**
 * The standard list renderer computes footer aggregates from the records loaded
 * on the current page. Cotizaciones Express needs totals for the complete
 * filtered result, regardless of the active page.
 */
export class CotizacionGlobalTotalsListRenderer extends ListRenderer {
    setup() {
        super.setup();

        this.globalTotals = useState({
            signature: null,
            amount_total: 0,
            amount_confirmed: 0,
        });
        this.requestedTotalsSignature = null;

        onWillStart(() => this.loadGlobalTotals());
        onWillRender(() => {
            void this.loadGlobalTotals();
        });
    }

    getTotalsSignature() {
        const list = this.props.list;
        return JSON.stringify({
            domain: list.domain,
            count: list.count,
            records: list.records.map((record) => [
                record.resId,
                record.data.amount_total,
                record.data.amount_confirmed,
            ]),
        });
    }

    async loadGlobalTotals() {
        const list = this.props.list;
        if (list.selection.length || list.isGrouped) {
            return;
        }

        const signature = this.getTotalsSignature();
        if (signature === this.requestedTotalsSignature) {
            return;
        }
        this.requestedTotalsSignature = signature;

        const domain = JSON.parse(JSON.stringify(list.domain));
        try {
            const groups = await this.orm.formattedReadGroup(
                list.resModel,
                domain,
                [],
                ["amount_total:sum", "amount_confirmed:sum"],
                { context: list.context }
            );
            if (signature !== this.requestedTotalsSignature) {
                return;
            }

            const totals = groups[0] || {};
            this.globalTotals.amount_total = totals["amount_total:sum"] || 0;
            this.globalTotals.amount_confirmed = totals["amount_confirmed:sum"] || 0;
            this.globalTotals.signature = signature;
        } catch (error) {
            // Keep Odoo's standard page total as a safe fallback if the global
            // aggregation cannot be loaded.
            if (signature === this.requestedTotalsSignature) {
                this.globalTotals.signature = null;
            }
        }
    }

    computeAggregates() {
        const aggregates = super.computeAggregates();
        const list = this.props.list;

        if (
            list.selection.length ||
            list.isGrouped ||
            this.globalTotals.signature !== this.getTotalsSignature()
        ) {
            return aggregates;
        }

        const currencyValue = list.records.find((record) => record.data.currency_id)?.data
            .currency_id;
        const currencyId = currencyValue?.id ?? currencyValue;

        for (const fieldName of TOTAL_FIELDS) {
            if (!(fieldName in aggregates)) {
                continue;
            }
            const value = this.globalTotals[fieldName];
            aggregates[fieldName] = {
                ...aggregates[fieldName],
                rawValue: value,
                value: formatMonetary(value, {
                    currencyId,
                    digits: this.fields[fieldName].digits,
                }),
            };
        }
        return aggregates;
    }
}

registry.category("views").add("cotizacion_global_totals_list", {
    ...listView,
    Renderer: CotizacionGlobalTotalsListRenderer,
});

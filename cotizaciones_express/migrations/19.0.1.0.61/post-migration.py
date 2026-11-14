from odoo import Command, SUPERUSER_ID, api


def migrate(cr, version):
    """Keep existing internal users authorized on the first secured upgrade."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    access_group = env.ref(
        'cotizaciones_express.group_cotizaciones_express_user',
        raise_if_not_found=False,
    )
    internal_group = env.ref('base.group_user', raise_if_not_found=False)
    if not access_group or not internal_group:
        return

    existing_internal_users = internal_group.all_user_ids.filtered('active')
    access_group.write({
        'user_ids': [Command.set((access_group.user_ids | existing_internal_users).ids)],
    })

# -*- coding: utf-8 -*-
# © 2011 Pexego Sistemas Informáticos (<http://www.pexego.es>)
# © 2015 Avanzosc (<http://www.avanzosc.es>)
# © 2015 Pedro M. Baeza (<http://www.serviciosbaeza.com>)
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html

from odoo import api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    @api.depends('order_line.agents.amount')
    def _compute_commission_total(self):
        for record in self:
            record.commission_total = 0.0
            for line in record.order_line:
                record.commission_total += sum(x.amount for x in line.agents)

    commission_total = fields.Float(
        string="Commissions", compute="_compute_commission_total",
        store=True)


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    agents = fields.One2many(
        string="Agents & commissions", comodel_name='sale.order.line.agent',
        inverse_name='sale_line', copy=True)
    commission_free = fields.Boolean(
        string="Comm. free", related="product_id.commission_free",
        store=True, readonly=True)

    @api.model
    def _prepare_order_line_invoice_line_ids(self, line, account_id=False):
        vals = super(SaleOrderLine, self)._prepare_order_line_invoice_line_ids(
            line, account_id=account_id)
        vals['agents'] = [
            (0, 0, {'agent': x.agent.id,
                    'commission': x.commission.id}) for x in line.agents]
        return vals

    @api.multi
    def _agent_unlink(self, order_line_ids):
        for line_id in order_line_ids:
            line_agents = self.env['sale.order.line.agent']
            line_agents = line_agents.search([('sale_line', '=', line_id)])
            for line in line_agents: line.unlink()

    @api.multi
    def _get_agents_commission(self):
        agents = []
        if not self.commission_free:
            for agent in self.order_id.partner_id.agents:
                if agent.commission:
                    commission = agent.commission
                else:
                    commission = self.product_id.commission or \
                                 self.product_id.categ_id.commission
                if commission:
                    agents.append(({'agent': agent.id,
                                    'commission': commission.id,
                                    'sale_line': self.id}))
                else:
                    raise UserError("Não foi encontrada nenhuma tabela de \
                    comissão para este produto")

            seller = self.order_id.user_id or False
            if seller and seller.agent and seller.agent_type=='internal':
                if seller.commission:
                    commission = seller.commission
                else:
                    commission = self.product_id.commission or \
                                 self.product_id.categ_id.commission
                agents.append(({'agent': seller.partner_id.id,
                                'commission': commission.id,
                                'sale_line': self.id}))
            for vals in agents:
                self.env['sale.order.line.agent'].create(vals)

    @api.multi
    def write (self, vals):
        res = super(SaleOrderLine,self).write(vals)
        self._agent_unlink([self.id])
        self._get_agents_commission()
        self.env['sale.order.line.agent'].calculate_amount(self.agents)
        return res

    @api.multi
    def unlink(self):
        res = super(SaleOrderLine,self).unlink()
        self._agent_unlink([self.id])
        return res

class SaleOrderLineAgent(models.Model):
    _name = "sale.order.line.agent"
    _rec_name = "agent"

    sale_line = fields.Many2one(
        comodel_name="sale.order.line", required=True, ondelete="cascade")
    agent = fields.Many2one(
        comodel_name="res.partner", required=True, ondelete="restrict",)
    commission = fields.Many2one(
        comodel_name="sale.commission", required=True, ondelete="restrict")
    amount = fields.Float('Amount', default=0.0)
    currency_id = fields.Many2one('res.currency', string='Currency',
        required=True, readonly=True, related="sale_line.currency_id")

    _sql_constraints = [
        ('unique_agent', 'UNIQUE(sale_line, agent)',
         'You can only add one time each agent.')]

    def calculate_amount(self, agents):
        for line in agents:
            line.amount = 0.0

            if line.commission.amount_base_type == 'net_amount':
                subtotal = (line.sale_line.price_subtotal -
                            (line.sale_line.product_id.standard_price *
                             line.sale_line.product_uom_qty))
            else:
                subtotal = line.sale_line.price_subtotal

            if line.commission.commission_type == 'fixed':
                fix_qty = line.commission.fix_qty
                if line.commission.rule_based:
                    line_agent_id = self.search(
                       [('commission', '=', line.commission.rule_based.id)])
                    base_commission = line_agent_id.amount
                    commission = base_commission * (fix_qty / 100.0)
                    bsae_commission -= commission
                    line_agent_id.write({'amount':base_commission})
                else:
                    commission = subtotal * (fix_qty / 100.0)

            elif line.commission.commission_type == 'section_value':
                percent = line.commission.percent_section(subtotal)
                commission = subtotal * percent / 100.0
            elif line.commission.commission_type == 'section_discount':
                percent = line.commission.percent_section(line.discount or 0.0)
                commission = subtotal * percent / 100.0

            # Busca por regras que por padrão apliquem a divisão da comissão.
            commission_divided = self.env['sale.commission'].search(
                [('rule_based','=',line.commission.id),
                ('commission_type','=','divided')])

            # Calcula o valor do rateio e divide entre os pertencentes da regra
            for rule in commission_divided:
                inherit_commission = commission * (rule.fix_qty / 100.0)
                commission -= inherit_commission
                agents = self.env['res.partner'].search(
                    [('agent','=', True),('commission', '=', rule.id)])
                part_commission = inherit_commission / len(agents)
                for agent in agents:
                    self.create({'sale_line': line.sale_line.id,
                                 'agent': agent.id,
                                 'commission': rule.id,
                                 'amount': part_commission,
                                 })
            line.amount = commission

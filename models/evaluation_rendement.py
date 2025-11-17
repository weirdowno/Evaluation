from odoo import api, fields, models
from odoo.exceptions import AccessError


class EvaluationRendement(models.Model):
    _name = "evaluation.rendement"
    _description = "Evaluation du rendement"
    _order = "date_saisie desc, id desc"

    name = fields.Char(required=False)

    employe_id = fields.Many2one(
        comodel_name="hr.employee",
        string="Employé",
        required=True,
    )
    validateur_n1_id = fields.Many2one(
        comodel_name="hr.employee",
        string="Validateur N+1",
        readonly=True,
    )

    # Saison
    semester = fields.Selection(
        selection=[("S1", "S1"), ("S2", "S2")],
        string="Saison",
        default=lambda self: "S1" if (fields.Date.context_today(self).month <= 6) else "S2",
        required=True,
    )
    year = fields.Integer(
        string="Année",
        default=lambda self: fields.Date.context_today(self).year,
        required=True,
    )

    date_saisie = fields.Date(string="Date de saisie", default=fields.Date.context_today, readonly=True)
    date_validation = fields.Date(string="Date de validation", readonly=True)

    # Etat du workflow
    state = fields.Selection(
        selection=[
            ("draft", "Brouillon"),
            ("to_validate_n1", "A valider N+1"),
            ("done", "Validé"),
        ],
        default="draft",
        tracking=True,
    )

    # Critères collectifs
    score_disponibilite = fields.Float(string="Disponibilité", default=0.0)
    score_accident_travail = fields.Float(string="Accidents de travail", default=0.0)
    score_accident_materiel = fields.Float(string="Accidents matériels", default=0.0)

    # Critères individuels
    score_temps_execution = fields.Float(string="Temps d'exécution", default=0.0)
    score_respect_procedures = fields.Float(string="Respect des procédures", default=0.0)
    score_absenteisme = fields.Float(string="Absenteïsme", default=0.0)
    score_discipline = fields.Float(string="Discipline", default=0.0)

    score_total = fields.Float(string="Score total", compute="_compute_score_total", store=False)

    # Champs techniques utilisés par les vues (attrs)
    can_validate = fields.Boolean(compute="_compute_rights_flags", store=False)
    can_edit_manager_comment = fields.Boolean(compute="_compute_rights_flags", store=False)

    commentaire_employe = fields.Text(string="Commentaire employé")
    commentaire_manager = fields.Text(string="Commentaire manager")

    _sql_constraints = [
        (
            "unique_employee_semester_year",
            "unique(employe_id, semester, year)",
            "Une seule évaluation par employé, saison et année est autorisée.",
        )
    ]

    @api.depends(
        "score_disponibilite",
        "score_accident_travail",
        "score_accident_materiel",
        "score_temps_execution",
        "score_respect_procedures",
        "score_absenteisme",
        "score_discipline",
    )
    def _compute_score_total(self):
        for record in self:
            record.score_total = (
                (record.score_disponibilite or 0.0)
                + (record.score_accident_travail or 0.0)
                + (record.score_accident_materiel or 0.0)
                + (record.score_temps_execution or 0.0)
                + (record.score_respect_procedures or 0.0)
                + (record.score_absenteisme or 0.0)
                + (record.score_discipline or 0.0)
            )

    @api.depends("state", "employe_id", "validateur_n1_id")
    def _compute_rights_flags(self):
        user = self.env.user
        for record in self:
            is_manager_n1 = (
                record.validateur_n1_id
                and record.validateur_n1_id.user_id
                and record.validateur_n1_id.user_id == user
            )
            record.can_validate = record.state == "to_validate_n1" and bool(is_manager_n1)
            record.can_edit_manager_comment = bool(is_manager_n1) and record.state != "draft"

    # --- Security helpers ---
    def _user_is_hr(self):
        return self.env.user.has_group("evaluation.group_evaluation_hr")

    def _user_is_direct_manager_of(self, employee):
        user_emp = self.env.user.employee_id
        return bool(user_emp and employee and employee.parent_id and employee.parent_id.id == user_emp.id)

    # --- CRUD guards ---
    @api.model
    def create(self, vals):
        if not self.env.su and not self._user_is_hr():
            employee = self.env["hr.employee"].browse(vals.get("employe_id")) if vals.get("employe_id") else None
            if not self._user_is_direct_manager_of(employee):
                raise AccessError("Vous ne pouvez créer qu'une évaluation pour vos N-1.")
        rec = super().create(vals)
        # Auto-assign manager if missing
        if not rec.validateur_n1_id and rec.employe_id and rec.employe_id.parent_id:
            rec.validateur_n1_id = rec.employe_id.parent_id
        return rec

    def write(self, vals):
        if not self.env.su and not self._user_is_hr():
            for record in self:
                target_employee = record.employe_id
                # If changing employee, check target
                if vals.get("employe_id"):
                    target_employee = self.env["hr.employee"].browse(vals.get("employe_id"))
                if not self._user_is_direct_manager_of(target_employee):
                    raise AccessError("Vous ne pouvez modifier qu'une évaluation pour vos N-1.")
        return super().write(vals)

    def unlink(self):
        if not self.env.su and not self._user_is_hr():
            raise AccessError("La suppression est réservée aux RH.")
        return super().unlink()

    def action_send_for_validation(self):
        for record in self:
            # Déterminer automatiquement le validateur N+1 si possible
            if not record.validateur_n1_id and record.employe_id:
                manager = record.employe_id.parent_id  # manager hiérarchique standard HR
                if manager:
                    record.validateur_n1_id = manager
            record.state = "to_validate_n1"
            if not record.date_saisie:
                record.date_saisie = fields.Date.context_today(self)
        return True

    def action_validate(self):
        for record in self:
            record.state = "done"
            record.date_validation = fields.Date.context_today(self)
        return True


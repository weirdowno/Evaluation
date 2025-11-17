from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


class Saison(models.Model):
    _name = "evaluation.saison"
    _description = "Saison d'évaluation"
    _order = "year desc, name desc"

    name = fields.Char(string="Saison", required=True)
    semester = fields.Selection(
        selection=[("S1", "S1"), ("S2", "S2")],
        string="Semestre",
        required=True,
    )
    year = fields.Integer(
        string="Année",
        required=True,
        default=lambda self: fields.Date.context_today(self).year
    )
    date_debut = fields.Date(string="Date de début", required=True)
    date_fin = fields.Date(string="Date de fin", required=True)
    active = fields.Boolean(string="Active", default=True)
    
    # Critères spécifiques à cette saison
    critere_ids = fields.Many2many(
        comodel_name='evaluation.critere',
        relation='saison_critere_rel',
        column1='saison_id',
        column2='critere_id',
        string='Critères de la saison'
    )

    _sql_constraints = [
        (
            "unique_semester_year",
            "unique(semester, year)",
            "Une seule saison par semestre et année est autorisée.",
        )
    ]

    @api.constrains('date_debut', 'date_fin')
    def _check_dates(self):
        for record in self:
            if record.date_debut and record.date_fin and record.date_debut > record.date_fin:
                raise ValidationError("La date de début doit être antérieure à la date de fin.")

    def action_copy_criteres_from_previous(self):
        """Copie les critères de la saison précédente vers cette saison"""
        self.ensure_one()
        
        # Trouver la saison précédente (même semestre, année-1)
        previous_saison = self.search([
            ('semester', '=', self.semester),
            ('year', '=', self.year - 1),
            ('id', '!=', self.id)
        ], limit=1)
        
        if previous_saison:
            self.critere_ids = previous_saison.critere_ids
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Critères copiés',
                    'message': f'Critères copiés depuis {previous_saison.display_name}',
                    'type': 'success',
                    'sticky': False,
                }
            }
        else:
            raise ValidationError("Aucune saison précédente trouvée pour copier les critères.")

    def name_get(self):
        result = []
        for record in self:
            name = f"{record.name} ({record.semester}-{record.year})"
            result.append((record.id, name))
        return result


class CritereEvaluation(models.Model):
    _name = "evaluation.critere"
    _description = "Critère d'évaluation"
    _order = "type_critere, sequence"

    name = fields.Char(string="Nom du critère", required=True)
    code = fields.Char(string="Code", required=True)
    type_critere = fields.Selection(
        selection=[
            ("collectif", "Critère Collectif"),
            ("individuel", "Critère Individuel")
        ],
        string="Type de critère",
        required=True
    )
    sequence = fields.Integer(string="Ordre", default=10)
    description = fields.Text(string="Description")
    poids = fields.Float(
        string="Poids",
        default=1.0,
        help="Poids du critère dans le calcul du score total"
    )
    score_min = fields.Float(
        string="Score minimum",
        default=0.0,
        help="Score minimum autorisé pour ce critère"
    )
    score_max = fields.Float(
        string="Score maximum", 
        default=100.0,
        help="Score maximum autorisé pour ce critère"
    )
    actif = fields.Boolean(string="Actif", default=True)

    _sql_constraints = [
        (
            "unique_code",
            "unique(code)",
            "Le code du critère doit être unique.",
        )
    ]


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
     
    # Référence à la saison
    saison_id = fields.Many2one(
        comodel_name="evaluation.saison",
        string="Saison d'évaluation",
        required=True,
    )
    
    # Champs dérivés pour compatibilité
    semester = fields.Selection(
        selection=[("S1", "S1"), ("S2", "S2")],
        string="Semestre",
        related="saison_id.semester",
        store=True
    )
    year = fields.Integer(
        string="Année",
        related="saison_id.year",
        store=True
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

    # Lignes de scores basées sur les critères
    ligne_critere_ids = fields.One2many(
        comodel_name="evaluation.rendement.ligne",
        inverse_name="evaluation_id",
        string="Scores"
    )

    score_total = fields.Float(string="Score total", compute="_compute_score_total", store=False)

    # Champs techniques utilisés par les vues (attrs)
    can_validate = fields.Boolean(compute="_compute_rights_flags", store=False)
    can_edit_manager_comment = fields.Boolean(compute="_compute_rights_flags", store=False)

    commentaire_employe = fields.Text(string="Commentaire employé")
    commentaire_manager = fields.Text(string="Commentaire manager")

    _sql_constraints = [
        (
            "unique_employee_saison",
            "unique(employe_id, saison_id)",
            "Une seule évaluation par employé et saison est autorisée.",
        )
    ]

    @api.depends("ligne_critere_ids.score")
    def _compute_score_total(self):
        for record in self:
            total = 0.0
            for ligne in record.ligne_critere_ids:
                total += (ligne.score or 0.0) * (ligne.critere_id.poids or 1.0)
            record.score_total = total

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

    @api.model
    def create(self, vals):
        if not self.env.su and not self._user_is_hr():
            employee = self.env["hr.employee"].browse(vals.get("employe_id")) if vals.get("employe_id") else None
            if not self._user_is_direct_manager_of(employee):
                raise AccessError("Vous ne pouvez créer qu'une évaluation pour vos N-1.")
        
        # Créer d'abord l'évaluation
        rec = super().create(vals)
        
        # Auto-assign manager if missing
        if not rec.validateur_n1_id and rec.employe_id and rec.employe_id.parent_id:
            rec.validateur_n1_id = rec.employe_id.parent_id
            
        # Créer automatiquement les lignes de critères APRÈS la création
        rec._create_lignes_criteres()
        
        return rec

    def _create_lignes_criteres(self):
        """Crée automatiquement les lignes de critères pour l'évaluation basée sur la saison"""
        if not self.saison_id:
            return
            
        # Utiliser les critères de la saison
        criteres_saison = self.saison_id.critere_ids.filtered(lambda c: c.actif)
        
        for critere in criteres_saison:
            self.env['evaluation.rendement.ligne'].create({
                'evaluation_id': self.id,
                'critere_id': critere.id,
                'score': 0.0
            })

    
    # --- Security helpers ---
    def _user_is_hr(self):
        return self.env.user.has_group("evaluation.group_evaluation_hr")

    def _user_is_direct_manager_of(self, employee):
        user_emp = self.env.user.employee_id
        return bool(user_emp and employee and employee.parent_id and employee.parent_id.id == user_emp.id)

    # --- CRUD guards ---
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
                manager = record.employe_id.parent_id
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
    def action_charger_criteres(self):
     for evaluation in self:
        # Supprimer les anciennes lignes
        evaluation.ligne_critere_ids.unlink()
        
        # UTILISER directement les critères de la saison
        criteres = evaluation.saison_id.critere_ids
        
        for critere in criteres:
            self.env['evaluation.rendement.ligne'].create({
                'evaluation_id': evaluation.id,
                'critere_id': critere.id,
                'score': 0,
                'commentaire': ''
            })
        
        


class EvaluationRendementLigne(models.Model):
    _name = "evaluation.rendement.ligne"
    _description = "Ligne de score par critère"

    evaluation_id = fields.Many2one(
        comodel_name="evaluation.rendement",
        string="Évaluation",
        required= "False",
        ondelete="cascade"
    )
    
    critere_id = fields.Many2one(
        comodel_name="evaluation.critere",
        string="Critère",
        required=True
    )
    type_critere = fields.Selection(
        related="critere_id.type_critere",
        string="Type de critère",
        store=True
    )
    score = fields.Float(string="Score", default=0.0)
    commentaire = fields.Text(string="Commentaire")
    score_max = fields.Float(
        string="Score max",
        related="critere_id.score_max",
        store=True,
        readonly=True
    )

    @api.constrains('score')
    def _check_score_range(self):
        for record in self:
            if record.critere_id and record.score is not False:
                min_score = record.critere_id.score_min
                max_score = record.critere_id.score_max
                
                if record.score < min_score or record.score > max_score:
                    raise ValidationError(
                        f"Le score pour le critère '{record.critere_id.name}' "
                        f"doit être entre {min_score} et {max_score}. "
                        f"Score saisi: {record.score}"
                    )

    _sql_constraints = [
        (
            "unique_critere_per_evaluation",
            "unique(evaluation_id, critere_id)",
            "Un critère ne peut apparaître qu'une fois par évaluation.",
        )
    ]

    @api.constrains('score')
    def _check_score_range(self):
      for record in self:
        if record.critere_id and record.score is not False:
            min_score = record.critere_id.score_min
            max_score = record.critere_id.score_max
            
            if record.score < min_score or record.score > max_score:
                raise ValidationError(
                    f"Le score pour le critère '{record.critere_id.name}' "
                    f"doit être entre {min_score} et {max_score}. "
                    f"Score saisi: {record.score}"
                )
    
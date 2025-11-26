{
    'name': 'evaluation',
    'version': '16.0.1.0.0',
    'category': 'Human Resources',
    'summary': 'Module d évaluation du rendement',
    'depends': ['base', 'hr'],
    'data': [
        'security/evaluation_security.xml',  
        'security/ir.model.access.csv',
        'data/critere_data.xml',  # Nouveau fichier de données
        'views/evaluation_views.xml',
    ],
    'installable': True,
    'application': True,
}
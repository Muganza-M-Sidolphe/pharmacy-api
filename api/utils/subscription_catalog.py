PLAN_CATALOG = [
    {
        "id": "starter",
        "name": "Starter",
        "description": "Essential tools for small pharmacies.",
        "type": "retail",
        "recommended": False,
        "pricing": {
            "monthly": {"amount": 25000, "currency": "RWF", "display": "RWF 25,000/month"},
            "annual": {
                "amount": 240000,
                "currency": "RWF",
                "monthly_equivalent": 20000,
                "savings": 60000,
                "display": "RWF 240,000/year",
                "savings_display": "Save RWF 60,000",
            },
        },
        "limits": {"users": 5, "branches": 1, "medicines": 1000, "transactions": 5000},
        "features": {
            "inventory_management": True,
            "sales_management": True,
            "expiry_alerts": True,
            "advanced_reports": False,
            "multi_branch_support": False,
            "priority_support": False,
            "collaborative_retail_orders": False,
        },
    },
    {
        "id": "growth",
        "name": "Growth",
        "description": "Advanced operations for growing pharmacies.",
        "type": "retail",
        "recommended": True,
        "pricing": {
            "monthly": {"amount": 60000, "currency": "RWF", "display": "RWF 60,000/month"},
            "annual": {
                "amount": 576000,
                "currency": "RWF",
                "monthly_equivalent": 48000,
                "savings": 144000,
                "display": "RWF 576,000/year",
                "savings_display": "Save RWF 144,000",
            },
        },
        "limits": {"users": 20, "branches": 5, "medicines": 10000, "transactions": 30000},
        "features": {
            "inventory_management": True,
            "sales_management": True,
            "expiry_alerts": True,
            "advanced_reports": True,
            "multi_branch_support": True,
            "priority_support": False,
            "collaborative_retail_orders": False,
        },
    },
    {
        "id": "enterprise",
        "name": "Enterprise",
        "description": "Full scale plan for chains and wholesalers.",
        "type": "wholesale",
        "recommended": False,
        "pricing": {
            "monthly": {"amount": 120000, "currency": "RWF", "display": "RWF 120,000/month"},
            "annual": {
                "amount": 1152000,
                "currency": "RWF",
                "monthly_equivalent": 96000,
                "savings": 288000,
                "display": "RWF 1,152,000/year",
                "savings_display": "Save RWF 288,000",
            },
        },
        "limits": {"users": 100, "branches": 50, "medicines": 100000, "transactions": 250000},
        "features": {
            "inventory_management": True,
            "sales_management": True,
            "expiry_alerts": True,
            "advanced_reports": True,
            "multi_branch_support": True,
            "priority_support": True,
            "collaborative_retail_orders": True,
        },
    },
]


def get_plan_by_id(plan_id):
    for plan in PLAN_CATALOG:
        if plan["id"] == plan_id:
            return plan
    return None

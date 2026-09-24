"""Permissions effectives d'un rôle (sans dépendance au contexte de requête)."""

from app.platform.access.models import Role
from app.platform.catalog.loader import role_templates
from app.platform.registry import ModuleRegistry


def effective_role_permissions(role: Role, registry: ModuleRegistry) -> set[str]:
    """Rôle système : permissions résolues depuis son modèle (toujours à jour du registre) ;
    rôle personnalisé : permissions enregistrées."""
    if role.is_system and role.template_code:
        template = role_templates().get(role.template_code)
        if template is not None:
            available = {p.code for m in registry.all() for p in m.permissions}
            return set(template.resolve(available))
    return {link.permission_code for link in role.permission_links}

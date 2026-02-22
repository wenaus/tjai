from .models import SysConfig


def health_status(request):
    """Add system health status to template context for menu coloring."""
    try:
        status = SysConfig.objects.filter(
            key='system_health_status'
        ).values_list('value', flat=True).first()
    except Exception:
        status = None
    colors = {'green': '#9ccc65', 'yellow': '#ffd54f', 'red': '#ef5350'}
    return {
        'health_status': status or '',
        'health_color': colors.get(status, '#8a8a8a'),
    }

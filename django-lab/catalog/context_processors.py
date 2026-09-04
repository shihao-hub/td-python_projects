import django


def globals(request):
    """自定义上下文处理器：模板层的全局扩展点。"""
    return {"django_version": django.get_version()}

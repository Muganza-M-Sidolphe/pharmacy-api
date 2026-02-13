from drf_spectacular.openapi import AutoSchema
from drf_spectacular.plumbing import build_serializer_context
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from rest_framework.views import APIView


class SafeAutoSchema(AutoSchema):
    """
    AutoSchema fallback for APIViews that don't declare serializers.
    This prevents schema generation from dropping such endpoints.
    """

    class _FallbackSerializer(serializers.Serializer):
        pass

    def _get_serializer(self):
        view = self.view
        context = build_serializer_context(view)

        try:
            if isinstance(view, GenericAPIView):
                if view.__class__.get_serializer == GenericAPIView.get_serializer:
                    return view.get_serializer_class()(context=context)
                return view.get_serializer(context=context)
            if isinstance(view, APIView):
                if callable(getattr(view, "get_serializer", None)):
                    return view.get_serializer(context=context)
                if callable(getattr(view, "get_serializer_class", None)):
                    return view.get_serializer_class()(context=context)
                if hasattr(view, "serializer_class"):
                    serializer_cls = view.serializer_class
                    if isinstance(serializer_cls, type):
                        return serializer_cls(context=context)
                    return serializer_cls
        except Exception:
            pass

        return self._FallbackSerializer(context=context)

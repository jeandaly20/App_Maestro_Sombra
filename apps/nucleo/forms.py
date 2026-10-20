from django import forms


class FormularioAccesible:
    """
    Da a cada control la clase de estilo y, si tiene errores, marca
    aria-invalid y lo une a su mensaje de error (WCAG 3.3.1 y 4.1.2).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if not isinstance(campo.widget, forms.CheckboxInput):
                campo.widget.attrs.setdefault('class', 'campo__control')

    def full_clean(self):
        super().full_clean()
        for nombre in self.errors:
            if nombre in self.fields:
                attrs = self.fields[nombre].widget.attrs
                attrs['aria-invalid'] = 'true'
                attrs['aria-describedby'] = f'{self[nombre].auto_id}_error'

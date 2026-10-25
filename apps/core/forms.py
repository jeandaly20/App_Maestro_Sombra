from django import forms


class AccessibleFormMixin:
    """
    Gives every control the style class and, when it has errors, sets
    aria-invalid and links it to its error message (WCAG 3.3.1 and 4.1.2).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            # Checkboxes and radio options are drawn as cards: the full-width text box style would
            # stretch their (invisible) inputs over the neighbouring options and steal their clicks
            if not isinstance(field.widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                field.widget.attrs.setdefault('class', 'field__control')

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                attrs = self.fields[name].widget.attrs
                attrs['aria-invalid'] = 'true'
                attrs['aria-describedby'] = f'{self[name].auto_id}_error'

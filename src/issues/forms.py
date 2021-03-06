from communities.models import CommunityConfidentialReason
from django.conf import settings
from django.utils.translation import ugettext_lazy as _
from issues import models
from issues.models import ProposalType
from ocd.formfields import HTMLArea, OCIssueRadioButtons, OCProposalRadioButtons
from users.models import OCUser
import floppyforms as forms


class CreateIssueForm(forms.ModelForm):

    class Meta:
        model = models.Issue
        fields = ('confidential_reason', 'title', 'abstract')
        widgets = {'title': forms.TextInput, 'abstract': HTMLArea,
                   'confidential_reason': OCIssueRadioButtons}

    def __init__(self, community=None, *args, **kwargs):
        super(CreateIssueForm, self).__init__(*args, **kwargs)
        self.new_proposal = CreateProposalBaseForm(community=community,
            prefix='proposal', data=self.data if self.is_bound else None)
        self.new_proposal.fields['type'].required = False
        self.fields['confidential_reason'].empty_label = _('Not Confidential')
        self.fields['confidential_reason'].queryset = community.confidential_reasons.all().order_by('id')

    def is_valid(self):
        valid = super(CreateIssueForm, self).is_valid()
        if self.data.get('proposal-type') == '':
            return valid
        return self.new_proposal.is_valid() and valid

    def save(self, commit=True):
        o = super(CreateIssueForm, self).save(commit)
        if self.data.get('proposal-type') != '':
            self.new_proposal.instance.issue = o
            self.new_proposal.instance.created_by = o.created_by
            self.new_proposal.save()
        return o


class UpdateIssueForm(forms.ModelForm):
    class Meta:
        model = models.Issue
        fields = ('confidential_reason', 'title')
        widgets = {
            'title': forms.TextInput,
            'confidential_reason': OCIssueRadioButtons
        }

    def __init__(self, community=None, *args, **kwargs):
        super(UpdateIssueForm, self).__init__(*args, **kwargs)
        self.fields['confidential_reason'].empty_label = _('Not Confidential')
        self.fields['confidential_reason'].queryset = \
            community.confidential_reasons.all().order_by('id')


class UpdateIssueAbstractForm(forms.ModelForm):
    class Meta:
        model = models.Issue
        fields = ('abstract',)
        widgets = {
            'abstract': HTMLArea,
        }


class AddAttachmentBaseForm(forms.ModelForm):
    class Meta:
        model = models.IssueAttachment
        fields = (
                   'title',
                   'file',
                   )

        widgets = {
            'title': forms.TextInput,
            }

    def clean_file(self):
        file_obj = self.cleaned_data['file']

        if len(file_obj.name.split('.')) == 1:
            raise forms.ValidationError(_("File type is not allowed!"))

        if file_obj.name.split('.')[-1].lower() not in settings.UPLOAD_ALLOWED_EXTS:
            raise forms.ValidationError(_("File type is not allowed!"))

        return file_obj


    def clean_title(self):
        title = self.cleaned_data['title']

        if len(title.strip()) == 0:
            raise forms.ValidationError(_("Title cannot be empty"))

        return title


class AddAttachmentForm(AddAttachmentBaseForm):
    submit_button_text = _('Upload')


class CreateProposalBaseForm(forms.ModelForm):

    class Meta:
        model = models.Proposal

        fields = ('confidential_reason', 'type', 'title', 'content', 'tags', 'assigned_to_user',
                  'assigned_to', 'due_by')

        widgets = {
            'type': forms.Select,
            'title': forms.TextInput,
            'content': HTMLArea,
            'assigned_to_user': forms.HiddenInput(),
            'assigned_to': forms.TextInput,
            'due_by': forms.DateInput,
            'confidential_reason': OCProposalRadioButtons
        }

    def __init__(self, community=None, *args, **kwargs):

        super(CreateProposalBaseForm, self).__init__(*args, **kwargs)

        self.fields['confidential_reason'].empty_label = _('Not Confidential')
        self.fields['confidential_reason'].queryset = community.confidential_reasons.all().order_by('id')


class CreateProposalForm(CreateProposalBaseForm):

    submit_button_text = _('Create')

    def __init__(self, *args, **kwargs):
        super(CreateProposalForm, self).__init__(*args, **kwargs)
        self.fields['type'].initial = ProposalType.ADMIN


class EditProposalForm(CreateProposalForm):
    submit_button_text = _('Save')
    def __init__(self, *args, **kwargs):
        super(EditProposalForm, self).__init__(*args, **kwargs)
        self.fields['type'].initial = self.instance.type


class EditProposalTaskForm(EditProposalForm):

    class Meta:
        model = models.Proposal
        fields = (
                   'assigned_to',
                   'due_by',
                   )


class CreateIssueCommentForm(forms.ModelForm):

    submit_label = _('Add')
    form_id = "add-comment"

    class Meta:
        model = models.IssueComment
        fields = (
                   'content',
                   )
        widgets = {
                    'content': HTMLArea,
                }

    def __init__(self, *args, **kwargs):
#         self.helper = FormHelper()
#         if self.form_id:
#             self.helper.form_id = self.form_id

#         self.helper.add_input(Submit('submit', self.submit_label))

        super(CreateIssueCommentForm, self).__init__(*args, **kwargs)


class EditIssueCommentForm(CreateIssueCommentForm):

    submit_label = _('Save')
    form_id = None


class CreateProposalVoteArgumentForm(forms.ModelForm):

    submit_label = _('Add')
    form_id = "add-argument"

    class Meta:
        model = models.ProposalVoteArgument
        fields = (
            'argument',
        )


class EditProposalVoteArgumentForm(CreateProposalVoteArgumentForm):

    submit_label = _('Save')
    form_id = None

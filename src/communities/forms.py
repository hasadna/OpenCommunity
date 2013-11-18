from django.utils.translation import ugettext as _
from communities.models import Community, SendToOption
from communities.models import Community, SendToOption, ContactUs
from django.utils.translation import ugettext_lazy as _
from ocd.formfields import HTMLArea, DateTimeLocalInput
from users.models import OCUser
from issues.models import Issue
import floppyforms as forms


class EditUpcomingMeetingForm(forms.ModelForm):

    class Meta:
        model = Community

        fields = (
                   'upcoming_meeting_title',
                   'upcoming_meeting_scheduled_at',
                   'upcoming_meeting_location',
                   'voting_ends_at',
                   'upcoming_meeting_comments',
                   )

        widgets = {
            'upcoming_meeting_title': forms.TextInput,
            'upcoming_meeting_scheduled_at': DateTimeLocalInput,
            'upcoming_meeting_location': forms.TextInput,
            'voting_ends_at': DateTimeLocalInput,
            'upcoming_meeting_comments': HTMLArea,
        }


class PublishUpcomingMeetingForm(forms.ModelForm):

    send_to = forms.TypedChoiceField(label=_("Send to"), coerce=int,
                                choices=SendToOption.choices,
                                widget=forms.RadioSelect)

    class Meta:
        model = Community

        fields = ()


class EditUpcomingMeetingSummaryForm(forms.ModelForm):

    class Meta:
        model = Community

        fields = (
                   'upcoming_meeting_summary',
                   )



class StartMeetingForm(forms.ModelForm):

    class Meta:
        model = Community

        fields = (
                  'upcoming_meeting_started',
                  )

        widgets = {
            'upcoming_meeting_started': forms.CheckboxInput,
        }


class UpcomingMeetingParticipantsForm(forms.ModelForm):

#     upcoming_meeting_participants = forms.ModelMultipleChoiceField(label=_(
#                                          "Participants in upcoming meeting"),
#                                        required=False,
#                                        queryset=OCUser.objects.all(),
#                                        widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = Community

        fields = (
                   'upcoming_meeting_participants',
                   'upcoming_meeting_guests',
                   )

        widgets = {
            'upcoming_meeting_participants': forms.CheckboxSelectMultiple,
            'upcoming_meeting_guests': forms.Textarea,
        }

    def __init__(self, *args, **kwargs):
        super(UpcomingMeetingParticipantsForm, self).__init__(*args, **kwargs)
        self.fields['upcoming_meeting_participants'].queryset = self.instance.get_members()

class ContactUsForm(forms.ModelForm):
    class Meta:
        model = ContactUs
        exclude = ('time',)
        #widgets = {
        #    'message': forms.Textarea(attrs={'cols': 80, 'rows': 20}),
        #    }
    contactName = forms.CharField(max_length=50,)
    contactName.required = True
    contactName.label = _('name')
    community = forms.ModelChoiceField(queryset=Community.objects.all())
    community.required = False
    community.label = _('community')
    issue = forms.ModelChoiceField(queryset=Issue.objects.all())
    issue.required = False
    issue.label = _('issue')
    email = forms.EmailField()
    email.required = True
    email.label = _('email')
    phone = forms.CharField(max_length=50,required=False,label=_('phone'))
    message = forms.CharField(widget=forms.Textarea)
    message.required = True
    message.label = _('message')
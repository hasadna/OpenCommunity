from django.http import HttpResponse,HttpResponseRedirect
from django.shortcuts import render_to_response
from django.template.context import RequestContext
from communities import models
from communities.forms import EditUpcomingMeetingForm, \
    PublishUpcomingMeetingForm, UpcomingMeetingParticipantsForm, StartMeetingForm, \
    EditUpcomingMeetingSummaryForm,ContactUsForm
from communities.models import SendToOption
from django.conf import settings
from django.contrib import messages
from django.db.models.aggregates import Max
from django.http.response import HttpResponse, HttpResponseBadRequest
from django.utils.translation import ugettext_lazy as _
from django.views.generic import ListView
from django.views.generic.detail import DetailView
from django.views.generic.edit import UpdateView
from ocd.base_views import ProtectedMixin, AjaxFormView
import datetime
import json
from communities.models import Community
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http.response import HttpResponseForbidden, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from users.permissions import has_community_perm, get_community_perms
from ocd.email import send_mails
import json
from django.views.generic.base import View
from issues.models import IssueStatus


class CommunityList(ListView):
    model = models.Community

    def get_queryset(self):
        qs = super(CommunityList, self).get_queryset()
        if self.request.user.is_superuser:
            return qs
        return qs.filter(is_public=True)

    def get_context_data(self, **kwargs):
        d = super(CommunityList, self).get_context_data(**kwargs)
        d['version'] = settings.OPENCOMMUNITY_VERSION
        return d


class CommunityModelMixin(ProtectedMixin):

    model = models.Community

    @property
    def community(self):
        return self.get_object()


class UpcomingMeetingView(CommunityModelMixin, DetailView):

    # TODO show empty page to 'issues.viewopen_issue'
    # TODO: show draft only to those allowed to manage it.
    required_permission = 'communities.access_community'

    template_name = "communities/upcoming.html"

    required_permission_for_post = 'community.editagenda_community'

    def post(self, request, *args, **kwargs):

        """ add / removes an issue from upcoming meeting """

        if settings.DEBUG:
            import time
            time.sleep(0.3)

        if 'issue' in request.POST:

            issue = self.get_object().issues.get(id=int(request.POST.get('issue')))
            if issue.changed_in_current():
                return HttpResponseBadRequest("can't remove this issue")
            add_to_meeting = request.POST['set'] == "0"
            issue.status = IssueStatus.IN_UPCOMING_MEETING if add_to_meeting \
                            else IssueStatus.OPEN
            last = self.get_object().upcoming_issues().aggregate(
                                     last=Max('order_in_upcoming_meeting'))['last']
            issue.order_in_upcoming_meeting = (last or 0) + 1
            issue.save()

            return HttpResponse(json.dumps(int(add_to_meeting)),
                                content_type='application/json')

        if 'issues[]' in request.POST:
            issues = [int(x) for x in request.POST.getlist('issues[]')]
            qs = self.get_object().upcoming_issues()
            for i, iid in enumerate(issues):
                qs.filter(id=iid).update(order_in_upcoming_meeting=i)

            return HttpResponse(json.dumps(True),
                                content_type='application/json')

        return HttpResponseBadRequest("Oops, bad request")


class PublishUpcomingMeetingPreviewView(CommunityModelMixin, DetailView):

    required_permission = 'communities.viewupcoming_community'

    template_name = "emails/agenda.html"


class EditUpcomingMeetingView(AjaxFormView, CommunityModelMixin, UpdateView):

    reload_on_success = True

    required_permission = 'community.editupcoming_community'
    
    form_class = EditUpcomingMeetingForm
    template_name = "communities/upcoming_form.html"

    def get_form(self, form_class):
        form = super(EditUpcomingMeetingView, self).get_form(form_class)
        c = self.get_object()
        if not c.straw_voting_enabled:
            del form.fields['voting_ends_at']

        return form

class EditUpcomingMeetingParticipantsView(AjaxFormView, CommunityModelMixin, UpdateView):

    reload_on_success = True

    required_permission = 'community.editparticipants_community'

    form_class = UpcomingMeetingParticipantsForm
    template_name = "communities/participants_form.html"


class PublishUpcomingView(AjaxFormView, CommunityModelMixin, UpdateView):

    reload_on_success = True

    required_permission = 'community.editagenda_community'

    form_class = PublishUpcomingMeetingForm
    template_name = "communities/publish_upcoming.html"

    def get_form(self, form_class):
        form = super(PublishUpcomingView, self).get_form(form_class)
        c = self.get_object()
        if not c.upcoming_meeting_started:
            form.fields['send_to'].choices = SendToOption.publish_choices

        return form

    def form_valid(self, form):

        resp = super(PublishUpcomingView, self).form_valid(form)

        c = self.object

        # increment agenda if publishing agenda.
        if not c.upcoming_meeting_started and form.cleaned_data['send_to'] != SendToOption.ONLY_ME:
            if form.cleaned_data['send_to'] == SendToOption.ALL_MEMBERS:
                c.upcoming_meeting_is_published = True
            else:
                c.upcoming_meeting_is_published = False
            c.upcoming_meeting_published_at = datetime.datetime.now()
            c.upcoming_meeting_version += 1

            c.save()

        template = 'protocol_draft' if c.upcoming_meeting_started else 'agenda'

        total = c.send_mail(template, self.request.user, form.cleaned_data['send_to'])
        messages.info(self.request, _("Sending to %d users") % total)

        return resp


class StartMeetingView(AjaxFormView, CommunityModelMixin, UpdateView):

    reload_on_success = True

    required_permission = 'community.editupcoming_community'

    form_class = StartMeetingForm

    template_name = "communities/start_meeting.html"


class EditUpcomingSummaryView(AjaxFormView, CommunityModelMixin, UpdateView):

    reload_on_success = True

    required_permission = 'community.editupcoming_community'

    form_class = EditUpcomingMeetingSummaryForm

    template_name = "communities/edit_summary.html"


class ProtocolDraftPreviewView(CommunityModelMixin, DetailView):

    required_permission = 'meetings.add_meeting'

    template_name = "emails/protocol_draft.html"


class ContactUsView(View):
    def get(self,request,pk):
        user = request.user
        form = ContactUsForm()
        community = Community.objects.get(pk = pk)
        form.fields['community'].initial = community
        get_response = True
        resp = render_to_response('communities/contact_us.html',context_instance=RequestContext(request,{
            'form':form,
            'get_response':get_response,
            'valid':form.is_valid(),
            'authenticated':user.is_authenticated()
        }))
        return resp
    def post(self,request,pk):
        form = ContactUsForm (request.POST) #bound form to contact
        get_response = False
        user = request.user
        resp = HttpResponse("")
        if user.is_authenticated() and form.is_valid():
            c=form.save(commit= True)
            send_mails(c.email, ['ehudmagal@gmail.com'], 'message from open community', c.message, '')
        return resp

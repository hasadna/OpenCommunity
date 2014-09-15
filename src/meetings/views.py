from itertools import chain
from django.contrib import messages
from django.http.response import HttpResponse
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from django.views.generic.base import RedirectView
from django.views.generic.detail import DetailView
from django.views.generic.edit import CreateView
from issues.views import CommunityMixin
from issues.models import Issue, IssueStatus
from meetings import models
from meetings.forms import CloseMeetingForm
from ocd.base_views import AjaxFormView
from communities.notifications import send_mail


class MeetingMixin(CommunityMixin):

    model = models.Meeting

    def get_queryset(self):
        return self.model.objects.filter(community=self.community)


class MeetingList(MeetingMixin, RedirectView):
    required_permission = 'meetings.view_meeting'

    def get_redirect_url(self, **kwargs):
        o = models.Meeting.objects.filter(community=self.community).latest('held_at')
        if o:
            return o.get_absolute_url()

    def get_context_data(self, **kwargs):
        context = super(MeetingDetailView, self).get_context_data(**kwargs)

        return context


class MeetingDetailView(MeetingMixin, DetailView):
    required_permission = 'meetings.view_meeting'

    def get_context_data(self, **kwargs):
        d = super(MeetingDetailView, self).get_context_data(**kwargs)
        o = self.get_object()
        d['guest_list'] = o.get_guest_list()
        d['total_participants'] = len(d['guest_list']) + o.participations \
                                    .filter(is_absent=False).count()
        d['agenda_items'] = self.object.agenda.object_access_control(
            user=self.request.user, community=self.community).all()
        for ai in d['agenda_items']:
            ai.restricted_accepted_proposals = ai.accepted_proposals(
                user=self.request.user, community=self.community)
        return d


class MeetingProtocolView(MeetingMixin, DetailView):
    required_permission = 'meetings.view_meeting'
    template_name = "emails/protocol.html"

    def get_context_data(self, **kwargs):
        context = super(MeetingProtocolView, self).get_context_data(**kwargs)
        agenda_items = context['object'].agenda.object_access_control(
            user=self.request.user, community=self.community).all()
        for ai in agenda_items:
            ai.accepted_proposals = ai.accepted_proposals(
                user=self.request.user, community=self.community)
            ai.rejected_proposals = ai.rejected_proposals(
                user=self.request.user, community=self.community)
            ai.proposals = ai.proposals(
                user=self.request.user, community=self.community)
        context['agenda_items'] = agenda_items
        return context


class MeetingCreateView(AjaxFormView, MeetingMixin, CreateView):
    """actualy, this view handles the "close meeting" form.
       meeting objects are created only after this act """

    required_permission = 'meetings.add_meeting'

    template_name = "meetings/meeting_close.html"
    form_class = CloseMeetingForm

    def get_initial(self):
        d = super(MeetingCreateView, self).get_initial()
        dt = self.community.upcoming_meeting_scheduled_at
        if not dt or dt > timezone.now():
            dt = timezone.now().replace(second=0)
        d["held_at"] = dt
        return d

    def get_context_data(self, **kwargs):
        d = super(MeetingCreateView, self).get_context_data(**kwargs)
        participants = self.community.upcoming_meeting_participants.all()
        d['no_participants'] = True if not participants else False
        d['issues_ready_to_close'] = self.community.issues_ready_to_close(
            user=self.request.user, community=self.community)
        return d

    def get_form_kwargs(self):
        kwargs = super(MeetingCreateView, self).get_form_kwargs()
        kwargs['issues'] = self.community.upcoming_issues(user=self.request.user)
        return kwargs


    def form_valid(self, form):
        # archive selected issues
        m = self.community.close_meeting(form.instance, self.request.user, self.community)
        Issue.objects.filter(id__in=form.cleaned_data['issues']).update(
                  completed=True, status=IssueStatus.ARCHIVED)
        total = send_mail(self.community, 'protocol', self.request.user,
                           form.cleaned_data['send_to'], {'meeting': m})
        messages.info(self.request, _("Sending to %d users") % total)
        return HttpResponse(m.get_absolute_url())

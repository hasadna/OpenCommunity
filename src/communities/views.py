import datetime
import json
from itertools import chain
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator, InvalidPage
from django.core.urlresolvers import reverse_lazy, reverse
from django.db.models.aggregates import Max
from django.http.response import HttpResponse, HttpResponseBadRequest,\
    HttpResponseRedirect, Http404
from django.shortcuts import render, redirect, render_to_response, get_object_or_404
from django.template import RequestContext
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from django.views.generic import View, ListView, TemplateView
from django.views.generic.detail import DetailView, SingleObjectMixin
from django.views.generic.edit import UpdateView, DeleteView

from communities import models
from communities.forms import EditUpcomingMeetingForm,\
    PublishUpcomingMeetingForm, UpcomingMeetingParticipantsForm,\
    EditUpcomingMeetingSummaryForm
from communities.models import SendToOption
from communities.notifications import send_mail
from haystack.inputs import AutoQuery
from issues.models import IssueStatus, Issue, Proposal
from meetings.models import Meeting
from ocd.base_views import ProtectedMixin, AjaxFormView
from ocd.base_managers import ConfidentialSearchQuerySet
from users.permissions import has_community_perm
from django.views.generic.base import RedirectView
from haystack.views import SearchView
from ocd.base_views import CommunityMixin
from django.contrib.auth.views import redirect_to_login
from django.http.response import HttpResponseForbidden, HttpResponse
from users.models import Membership
from forms import CommunitySearchForm


class LandingPage(TemplateView):

    template_name = 'communities/landing.html'


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

    def get(self, request, *args, **kwargs):
        if not has_community_perm(request.user, self.community,
            'communities.viewupcoming_draft')\
        and not self.community.upcoming_meeting_is_published:
            try:
                last_meeting = Meeting.objects.filter(community=self.community)\
                .latest('held_at')
                return HttpResponseRedirect(reverse('meeting',
                    kwargs={
                        'community_id': self.community.id,
                        'pk': last_meeting.id}))
            except Meeting.DoesNotExist:
                pass

        return super(UpcomingMeetingView, self).get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """ add / removes an issue from upcoming meeting """

        if settings.DEBUG:
            import time

            time.sleep(0.3)

        if 'issue' in request.POST:
            issue = self.get_object().issues.get(id=int(request.POST.get('issue')))
            if issue.changed_in_current():
                return HttpResponseBadRequest("Can't remove this issue")
            add_to_meeting = request.POST['set'] == "0"
            issue.status = IssueStatus.IN_UPCOMING_MEETING if add_to_meeting\
            else IssueStatus.OPEN
            last = self.get_object().upcoming_issues(user=self.request.user).aggregate(
                last=Max('order_in_upcoming_meeting'))['last']
            issue.order_in_upcoming_meeting = (last or 0) + 1
            issue.save()

            return HttpResponse(json.dumps(int(add_to_meeting)),
                content_type='application/json')

        if 'issues[]' in request.POST:
            issues = [int(x) for x in request.POST.getlist('issues[]')]
            qs = self.get_object().upcoming_issues(user=self.request.user)
            for i, iid in enumerate(issues):
                qs.filter(id=iid).update(order_in_upcoming_meeting=i)

            return HttpResponse(json.dumps(True),
                content_type='application/json')

        return HttpResponseBadRequest("Oops, bad request")

    def get_context_data(self, **kwargs):
        d = super(UpcomingMeetingView, self).get_context_data(**kwargs)
        sorted_issues = {'by_time': [], 'by_rank': []}
        open_issues = Issue.objects.filter(active=True,\
            community=self.community)\
        .exclude(status=IssueStatus.ARCHIVED)
        for i in open_issues.order_by('-created_at'):
            sorted_issues['by_time'].append(i.id)
        for i in open_issues.order_by('-order_by_votes'):
            sorted_issues['by_rank'].append(i.id)
        d['sorted'] = json.dumps(sorted_issues)

        d['upcoming_issues'] = self.object.upcoming_issues(user=self.request.user)
        d['available_issues'] = self.object.available_issues(user=self.request.user)
        d['has_straw_votes'] = self.object.has_straw_votes(
            user=self.request.user, community=self.community)
        return d


class PublishUpcomingMeetingPreviewView(CommunityModelMixin, DetailView):
    required_permission = 'communities.viewupcoming_community'
    template_name = "emails/agenda.html"

    def get_context_data(self, **kwargs):
        d = super(PublishUpcomingMeetingPreviewView, self).get_context_data(**kwargs)
        d['can_straw_vote'] = self.community.upcoming_proposals_any(
            {'is_open': True}, user=self.request.user, community=self.community)\
        and self.community.upcoming_meeting_is_published
        upcoming_issues = self.community.upcoming_issues(user=self.request.user)
        d['issue_container'] = []
        for i in upcoming_issues:
            proposals = i.proposals.object_access_control(
                user=self.request.user, community=self.community)
            d['issue_container'] .append({'issue': i, 'proposals': proposals})
        return d


class EditUpcomingMeetingView(AjaxFormView, CommunityModelMixin, UpdateView):
    reload_on_success = True

    required_permission = 'community.editupcoming_community'

    form_class = EditUpcomingMeetingForm
    template_name = "communities/upcoming_form.html"

    def get_form(self, form_class):
        form = super(EditUpcomingMeetingView, self).get_form(form_class)
        c = self.get_object()
        return form


class EditUpcomingMeetingParticipantsView(AjaxFormView, CommunityModelMixin, UpdateView):
    reload_on_success = True
    required_permission = 'community.editparticipants_community'
    form_class = UpcomingMeetingParticipantsForm
    template_name = "communities/participants_form.html"


class DeleteParticipantView(CommunityModelMixin, DeleteView):

#     required_permission = ''

    def get(self, request, *args, **kwargs):
        return HttpResponse("?")

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.object.delete()
        return HttpResponse("OK")


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
            c.upcoming_meeting_published_at = datetime.datetime.now()
            c.upcoming_meeting_version += 1

            c.save()

        template = 'protocol_draft' if c.upcoming_meeting_started else 'agenda'
        tpl_data = {
            'meeting_time': datetime.datetime.now().replace(second=0),
            'can_straw_vote': c.upcoming_proposals_any({'is_open': True},
                                                       user=self.request.user,
                                                       community=self.object)\
            and c.upcoming_meeting_is_published,
        }
        total = send_mail(c, template, self.request.user, form.cleaned_data['send_to'], tpl_data)
        messages.info(self.request, _("Sending to %d users") % total)

        return resp


class StartMeetingView(EditUpcomingMeetingParticipantsView):
    template_name = "communities/start_meeting.html"

    reload_on_success = True

    def on_success(self, form):
        c = self.object
        if not c.upcoming_meeting_started:
            c.upcoming_meeting_started = True
            c.voting_ends_at = timezone.now().replace(second=0)
            c.save()
            c.sum_vote_results()


class EndMeetingView(CommunityModelMixin, SingleObjectMixin, View):
    def post(self, request, *args, **kwargs):
        c = self.community
        if c.upcoming_meeting_started:
            c.upcoming_meeting_started = False
            c.voting_ends_at = timezone.now() + datetime.timedelta(days=4000)
            c.save()
        return redirect(c)


class EditUpcomingSummaryView(AjaxFormView, CommunityModelMixin, UpdateView):
    reload_on_success = True

    required_permission = 'community.editupcoming_community'

    form_class = EditUpcomingMeetingSummaryForm

    template_name = "communities/edit_summary.html"


class ProtocolDraftPreviewView(CommunityModelMixin, DetailView):
    required_permission = 'meetings.add_meeting'

    template_name = "emails/protocol_draft.html"

    def get_context_data(self, **kwargs):
        d = super(ProtocolDraftPreviewView, self).get_context_data(**kwargs)

        meeting_time = self.community.upcoming_meeting_scheduled_at
        if not meeting_time:
            meeting_time = datetime.datetime.now()

        draft_agenda_payload = []
        issue_status = IssueStatus.IS_UPCOMING
        issues = self.community.issues.filter(
            active=True, status__in=(issue_status)).order_by(
            'order_in_upcoming_meeting')

        for issue in issues:
            proposals = issue.proposals.object_access_control(
                user=self.request.user, community=self.community)
            draft_agenda_payload.append({
                'issue': issue,
                'proposals': proposals,
            })

        agenda_items = d['object'].draft_agenda(draft_agenda_payload)
        d['meeting_time'] = meeting_time.replace(second=0)
        d['agenda_items'] = agenda_items
        return d


class SumVotesView(View):
    required_permission = 'meetings.add_meeting'

    def get(self, request, pk):
        c = models.Community.objects.get(pk=pk)
        c.sum_vote_results(only_when_over=False)
        c.voting_ends_at = datetime.datetime.now().replace(second=0)
        c.save()
        return HttpResponseRedirect(reverse('community', kwargs={'pk': pk}))


class About(RedirectView):
    """ About the project page, for now just temporary redirect to Hasadna website """

    permanent = False
    url = 'http://www.hasadna.org.il/projects/odc/'


class CommunitySearchView(CommunityModelMixin, DetailView):
    template_name = 'search/search.html'
    paginate_by = 20
    model_names = {'proposal': Proposal,
              'issue': Issue}

    def paginate(self, sqs):
        try:
            page_no = int(self.request.GET.get('page', 1))
        except (TypeError, ValueError):
            raise Http404(_("Not a valid number for page."))

        if page_no < 1:
            raise Http404(_("Pages should be 1 or greater."))

        start_offset = (page_no - 1) * self.paginate_by

        paginator = Paginator(sqs, self.paginate_by)

        try:
            page = paginator.page(page_no)
        except InvalidPage:
            raise Http404(_("No such page!"))

        return page

    def get_term(self):
        return self.request.GET.get('q', '').strip()

    def get_model(self):
        return self.request.GET.get('type', '').strip()

    def get_sqs(self):
        return ConfidentialSearchQuerySet().object_access_control(
            user=self.request.user, community=self.community).filter(
            community=self.community.id)

    def get(self, request, *args, **kwargs):
        term = self.get_term()
        if not term:
            return super(CommunitySearchView, self).get(request, *args, **kwargs)
        sqs = self.get_sqs()
        model_name = self.get_model()
        model = self.model_names.get(model_name)
        if model:
            sqs = sqs.models(model)
        sqs = sqs.auto_query(term)
        sqs = sqs.load_all()
        page = self.paginate(sqs)
        self.object = self.get_object()
        context = self.get_context_data(object=self.object, query=term, paginator=page.paginator, page=page,
            **kwargs)
        if model_name in self.model_names.keys():
            context['type'] = model_name
            context['real_count'] = len([result for result in page.object_list if result])

        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        d = super(CommunitySearchView, self).get_context_data(**kwargs)
        d['query'] = self.get_term()
        return d

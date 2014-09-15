from django.conf import settings
from django.db.models.aggregates import Max
from django.http.response import HttpResponse, HttpResponseBadRequest, \
    HttpResponseForbidden
from django.shortcuts import get_object_or_404, render, redirect
from django.template import RequestContext
from django.template.loader import render_to_string
from django.views.generic import ListView
from django.views.generic.base import View
from django.views.generic.detail import DetailView, SingleObjectMixin
from django.views.generic.edit import CreateView, UpdateView, DeleteView
from issues import models, forms
from issues.forms import CreateIssueForm, CreateProposalForm, EditProposalForm, \
    UpdateIssueForm, EditProposalTaskForm, AddAttachmentForm, \
    UpdateIssueAbstractForm
from issues.models import ProposalType, Issue, IssueStatus, ProposalVote, \
    Proposal, ProposalVoteBoard, ProposalVoteValue, VoteResult
from meetings.models import Meeting
from oc_util.templatetags.opencommunity import minutes, board_voters_on_proposal
from ocd.base_views import CommunityMixin, AjaxFormView, json_response
from ocd.validation import enhance_html
from ocd.base_managers import ConfidentialSearchQuerySet
#from shultze_vote import send_issue_ranking
from users.default_roles import DefaultGroups
from users.models import Membership
from users.permissions import has_community_perm
from haystack.views import SearchView
from haystack.inputs import AutoQuery
from haystack.utils import Highlighter
import json
import mimetypes
from datetime import date

if getattr(settings, 'USE_SHULTZE', False):
    from shultze_vote import send_issue_ranking
else:
    from stubs.order_issues import send_issue_ranking
    

class IssueMixin(CommunityMixin):

    model = models.Issue

    def get_queryset(self):
        return self.model.objects.object_access_control(
            user=self.request.user,
            community=self.community).filter(community=self.community,
                                             active=True)


class ProposalMixin(IssueMixin):

    model = models.Proposal

    def get_queryset(self):
        return self.model.objects.object_access_control(
            user=self.request.user,
            community=self.community).filter(issue=self.issue,
                                             active=True)

    @property
    def issue(self):
        return get_object_or_404(models.Issue, community=self.community,
                                 pk=self.kwargs['issue_id'])

    def _can_complete_task(self):
        o = self.get_object()
        if self.request.user == o.assigned_to_user:
            return True
        return has_community_perm(self.request.user, self.community,
                                  'issues.edittask_proposal')


class IssueList(IssueMixin, ListView):

    required_permission = 'issues.viewopen_issue'

    def get_queryset(self):
        return super(IssueList, self).get_queryset().exclude(
            status=IssueStatus.ARCHIVED).order_by('-created_at')

    def get_context_data(self, **kwargs):
        d = super(IssueList, self).get_context_data(**kwargs)
        available_ids = set([x.id for x in self.get_queryset()])
        if d['community'].issue_ranking_enabled:
            d['sorted_issues'] = super(IssueList, self).get_queryset().exclude(
                                    status=IssueStatus.ARCHIVED).order_by('-order_by_votes')
            if d['cperms']['issues'].has_key('vote_ranking') and \
                                     self.request.user.is_authenticated():
                my_ranking = models.IssueRankingVote.objects.filter(
                                voted_by=self.request.user,
                                issue__community_id=d['community'].id) \
                                .order_by('rank')
                d['my_vote'] = [i.issue for i in my_ranking if i.issue.active and \
                                                i.issue.status != IssueStatus.ARCHIVED]

                d['my_non_ranked'] = [i for i in self.get_queryset() \
                                      if i not in d['my_vote']]

        for obj in self.object_list:
            obj.restricted_proposals = \
                obj.proposals.object_access_control(
                    user=self.request.user, community=self.community)
            for ai in obj.agenda_items.all():
                ai.restricted_proposals = ai.proposals(
                    user=self.request.user, community=self.community)
                ai.restricted_accepted_proposals = ai.accepted_proposals(
                    user=self.request.user, community=self.community)
        return d


    required_permission_for_post = 'issues.vote_ranking'

    def post(self, request, *args, **kwargs):
        # TODO: check post permission for user and for each issue
        send_issue_ranking(request)
        return json_response({'res': 'ok', })


class IssueDetailView(IssueMixin, DetailView):

    def get_required_permission(self):
        o = self.get_object()
        return 'issues.viewclosed_issue' if o.is_published else \
            'issues.viewopen_issue'

    def get_context_data(self, **kwargs):
        d = super(IssueDetailView, self).get_context_data(**kwargs)
        m_id = self.request.GET.get('m_id', None)
        d['form'] = forms.CreateIssueCommentForm()
        d['proposal_form'] = forms.CreateProposalForm(community=self.community)
        if m_id:
            d['meeting'] = get_object_or_404(Meeting, id=m_id,
                                             community=self.community)
            a = d['meeting'].agenda.object_access_control(
                user=self.request.user, community=self.community).all()
            d['meeting_active_issues'] = [ai.issue for ai in a if
                                          ai.issue.active]

        else:
            d['meeting'] = None

        if self.request.GET.get('s', None) == '1':
            d['all_issues'] = self.get_queryset().exclude(
                 status=IssueStatus.ARCHIVED).order_by('-created_at')
        o = self.get_object()
        group = self.request.user.get_default_group(o.community) \
                if self.request.user.is_authenticated() \
                else ''

        if group == DefaultGroups.BOARD or \
           group == DefaultGroups.SECRETARY:
            if o.is_current and self.request.user in \
               o.community.upcoming_meeting_participants.all():
                d['can_board_vote_self'] = True

        d['proposals'] = self.object.proposals.object_access_control(
                user=self.request.user, community=self.community).open()

        d['upcoming_issues'] = self.object.community.upcoming_issues(
                user=self.request.user, community=self.community)

        d['agenda_items'] = self.object.agenda_items.all()
        for ai in d['agenda_items']:
            ai.accepted_proposals = ai.accepted_proposals(
                user=self.request.user, community=self.community)
            ai.rejected_proposals = ai.rejected_proposals(
                user=self.request.user, community=self.community)
            ai.proposals = ai.proposals(
                user=self.request.user, community=self.community)

        return d


    required_permission_for_post = 'issues.add_issuecomment'


    def post(self, request, *args, **kwargs):

        form = forms.CreateIssueCommentForm(request.POST)
        if not form.is_valid():
            return HttpResponseBadRequest()

        i = self.get_object()
        c = i.comments.create(content=enhance_html(form.cleaned_data['content']),
                              created_by=request.user)

        self.object = i  # this makes the next line work
        context = self.get_context_data(object=i, c=c)
        return render(request, 'issues/_comment.html', context)


class IssueCommentMixin(CommunityMixin):
    model = models.IssueComment

    def get_required_permission(self):
        o = self.get_object()
        return 'issues.editopen_issuecomment' if o.issue.is_upcoming else \
            'issues.editclosed_issuecomment'

    def get_queryset(self):
        return models.IssueComment.objects.filter(issue__community=self.community)


class IssueCommentDeleteView(IssueCommentMixin, DeleteView):

    def post(self, request, *args, **kwargs):
        o = self.get_object()
        o.active = 'undelete' in request.POST
        o.save()
        return HttpResponse(int(o.active))


class IssueCommentEditView(IssueCommentMixin, UpdateView):

    form_class = forms.EditIssueCommentForm

    def form_valid(self, form):
        c = self.get_object()
        c.update_content(form.instance.version, self.request.user,
                                     form.cleaned_data['content'])

        context = self.get_context_data(object=c.issue, c=c)
        return render(self.request, 'issues/_comment.html', context)

    def form_invalid(self, form):
        return HttpResponse("")

    def get_form_kwargs(self):
        d = super(IssueCommentEditView, self).get_form_kwargs()
        d['prefix'] = 'ic%d' % self.get_object().id
        return d


class IssueCreateView(AjaxFormView, IssueMixin, CreateView):
    form_class = CreateIssueForm
    template_name = "issues/issue_create_form.html"
    reload_on_success = True

    def get_required_permission(self):
        return 'community.editagenda_community' if self.upcoming else 'issues.add_issue'

    upcoming = False

    def form_valid(self, form):
        form.instance.community = self.community
        form.instance.created_by = self.request.user
        form.instance.status = IssueStatus.IN_UPCOMING_MEETING if \
                                     self.upcoming else IssueStatus.OPEN
        if self.upcoming:
            max_upcoming = Issue.objects.filter(
                                community=self.community).aggregate(x=Max(
                                             'order_in_upcoming_meeting'))['x']
            form.instance.order_in_upcoming_meeting = max_upcoming + 1 \
                                                        if max_upcoming else 1

        return super(IssueCreateView, self).form_valid(form)

    def get_form_kwargs(self):
        kwargs = super(IssueCreateView, self).get_form_kwargs()
        kwargs.update({'community': self.community})
        return kwargs

    def get_success_url(self):
        url = super(IssueCreateView, self).get_success_url()
        if not self.upcoming:
            url += '?s=1'
        return url


class IssueEditView(AjaxFormView, IssueMixin, UpdateView):

    required_permission = 'issues.editopen_issue'

    form_class = UpdateIssueForm
    reload_on_success = True

    def form_valid(self, form):
        if self.reload_on_success:
            return super(IssueEditView, self).form_valid(form)
        else:
            self.object = form.save()
            return render(self.request, 'issues/_issue_title.html',
                          self.get_context_data())

    def get_form_kwargs(self):
        kwargs = super(IssueEditView, self).get_form_kwargs()
        kwargs.update({'community': self.community})
        return kwargs


class IssueEditAbstractView(AjaxFormView, IssueMixin, UpdateView):

    required_permission = 'issues.editopen_issue'

    form_class = UpdateIssueAbstractForm

    def form_valid(self, form):
        self.object = form.save()
        return render(self.request, 'issues/_issue-abstract.html',
                      self.get_context_data())


class IssueCompleteView(IssueMixin, SingleObjectMixin, View):

    required_permission = 'meetings.add_meeting'

    def post(self, request, *args, **kwargs):
        o = self.get_object()
        # TODO: verify that issue is in active meeting
        if request.POST.get('complete') == '1':
            o.completed = True
        elif request.POST.get('undo_complete') == '1':
            o.completed = False
            if o.status == IssueStatus.ARCHIVED:
                o.status = o.statuses.OPEN
        elif request.POST.get('archive') == '1':
            # TODO: check if issue can be closed
            o.completed = True
            o.status = IssueStatus.ARCHIVED
        o.save()
        return HttpResponse("-")


class IssueSetLengthView(IssueMixin, SingleObjectMixin, View):

    required_permission = 'community.editagenda_community'

    def post(self, request, *args, **kwargs):
        o = self.get_object()
        s = request.POST.get('length', '').strip()
        if s:
            try:
                t = int(s)
                if not 0 <= t <= 360:
                    raise ValueError('Illegal Value')
            except ValueError:
                return HttpResponseBadRequest("Bad Request")
        else:
            t = None
        o.length_in_minutes = t
        o.save()
        return HttpResponse(minutes(t) or "--:--")


class IssueDeleteView(AjaxFormView, IssueMixin, DeleteView):

    def get_required_permission(self):
        o = self.get_object()
        if o.is_published:
            return 'issues.editclosed_issue'

        return 'issues.add_issue' if o.created_by == self.request.user \
            else 'issues.editopen_issue'

    def get_success_url(self):
        return "" if self.issue.active else "-"

    def delete(self, request, *args, **kwargs):
        o = self.get_object()
        o.active = False
        o.save()
        o.active_proposals().update(active=False)
        return HttpResponse("-")


class AttachmentCreateView(AjaxFormView, IssueMixin, CreateView):
    model = models.IssueAttachment
    form_class = AddAttachmentForm

    required_permission = 'issues.editopen_issue'
    reload_on_success = True

    @property
    def issue(self):
        return get_object_or_404(models.Issue, community=self.community, pk=self.kwargs['pk'])

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.issue = self.issue
        return super(AttachmentCreateView, self).form_valid(form)


class AttachmentDeleteView(AjaxFormView, CommunityMixin, DeleteView):
    model = models.IssueAttachment
    required_permission = 'issues.editopen_issue'

    @property
    def issue(self):
        return get_object_or_404(models.Issue, pk=self.kwargs['issue_id'])

    def delete(self, request, *args, **kwargs):
        o = self.get_object()
        o.file.delete(save=False)
        o.delete()
        return HttpResponse("")


class AttachmentDownloadView(CommunityMixin, SingleObjectMixin, View):

    model = models.IssueAttachment

    def get_required_permission(self):
        o = self.get_object().issue
        return 'issues.viewclosed_issue' if o.is_published else \
            'issues.viewopen_issue'

    def get(self, request, *args, **kwargs):
        o = self.get_object()
        filename = o.file.name.split('/')[-1]
        mime_type = mimetypes.guess_type(filename, True)[0] or "text/plain"
        response = HttpResponse(o.file, content_type=mime_type)
        response['Content-Disposition'] = 'attachment; filename=%s' % filename.encode('utf-8')
        return response


class ProposalCreateView(AjaxFormView, ProposalMixin, CreateView):

    reload_on_success = True

    def get_required_permission(self):
        return 'issues.editclosedproposal' if \
            self.issue.status == IssueStatus.ARCHIVED \
            else 'issues.add_proposal'

    form_class = CreateProposalForm

    def get_context_data(self, **kwargs):
        context = super(ProposalCreateView, self).get_context_data(**kwargs)
        context['issue'] = self.issue

        return context

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.issue = self.issue
        if self.reload_on_success:
            return super(ProposalCreateView, self).form_valid(form)
        else:
            self.object = form.save()
            return render(self.request, 'issues/_proposal.html',
                          self.get_context_data(proposal=self.object))

    def get_success_url(self):
        return self.issue.get_absolute_url()

    def get_form_kwargs(self):
        d = super(ProposalCreateView, self).get_form_kwargs()
        d['prefix'] = 'proposal'
        d['community'] = self.community
        d['initial'] = {'issue': self.issue}
        return d


class ProposalDetailView(ProposalMixin, DetailView):

    def get_required_permission(self):
        p = self.get_object()
        return 'issues.viewclosed_proposal' if p.decided_at_meeting else 'issues.viewopen_proposal'

    def get_required_permission_for_post(self):
        p = self.get_object()
        return 'issues.acceptclosed_proposal' if p.decided_at_meeting else 'issues.acceptopen_proposal'

    def board_votes_dict(self):
        total_votes = 0
        votes_dict = { 'sums': {}, 'total': total_votes, 'per_user': {} }
        pro_count = 0
        con_count = 0
        neut_count = 0
        board_attending = self.community.meeting_participants()['board']

        for u in board_attending:
            vote = ProposalVoteBoard.objects.filter(proposal=self.get_object,
                                                    user=u)
            if vote.exists():
                votes_dict['per_user'][u] = vote[0]
                if vote[0].value == 1:
                    pro_count += 1
                    total_votes += 1
                elif vote[0].value == -1:
                    con_count += 1
                    total_votes += 1
                elif vote[0].value == 0:
                    neut_count += 1

            else:
                votes_dict['per_user'][u] = None
                neut_count += 1

        votes_dict['sums']['pro_count'] = pro_count
        votes_dict['sums']['con_count'] = con_count
        votes_dict['sums']['neut_count'] = neut_count
        votes_dict['total'] = total_votes
        return votes_dict

    def _init_board_votes(self, board_attending):
        p = self.get_object()
        for b in board_attending:
            ProposalVoteBoard.objects.create(proposal=p, user=b,
                                             voted_by_chairman=True)

    def get_context_data(self, **kwargs):
        """add meeting for the latest straw voting result
           add 'previous_res' var if found previous registered results for this meeting
        """
        context = super(ProposalDetailView, self).get_context_data(**kwargs)
        m_id = self.request.GET.get('m_id', None)
        o = self.get_object()

        if m_id:
            context['meeting_context'] = get_object_or_404(Meeting, id=m_id,
                                                    community=self.community)
            participants = context['meeting_context'].participants.all()
        else:
            context['meeting_context'] = None
            participants = o.issue.community.upcoming_meeting_participants.all()

        try:
            group = self.request.user.memberships.get(community=self.issue.community).default_group_name
        except:
            group = ""

        board_votes = ProposalVoteBoard.objects.filter(proposal=o)
        board_attending = board_voters_on_proposal(o)

        is_current = o.issue.is_current
        context['res'] = o.get_straw_results()

        results = VoteResult.objects.filter(proposal=o) \
                                    .order_by('-meeting__held_at')

        if o.issue.is_upcoming and \
           self.community.upcoming_meeting_is_published and \
           self.community.straw_vote_ended:
            context['meeting'] = self.community.draft_meeting()
        else:
            if results.count():
                context['meeting'] = results[0].meeting
            else:
                context['meeting'] = None

        if not board_votes.exists():
            self._init_board_votes(board_attending)
        show_to_member = group == DefaultGroups.MEMBER and o.decided_at_meeting
        show_to_board = (group == DefaultGroups.BOARD or \
                         group == DefaultGroups.SECRETARY) and \
                        (is_current or o.decided_at_meeting)
        show_to_chairman = group == DefaultGroups.CHAIRMAN and o.decided
        show_board_vote_result = o.register_board_votes and \
                                 board_votes.exclude(
                                   value=ProposalVoteValue.NEUTRAL).count() and \
                                 (show_to_member or show_to_board or show_to_chairman)
        context['issue_frame'] = self.request.GET.get('s', None)
        context['board_attending'] = board_attending
        context['user_vote'] = o.board_vote_by_member(self.request.user.id)
        context['show_board_vote_result'] = show_board_vote_result
        context['chairman_can_vote'] = is_current and not o.decided
        context['board_votes'] = self.board_votes_dict()
        context['can_board_vote_self'] = is_current and not o.decided and \
                                      (group == DefaultGroups.BOARD or \
                                       group == DefaultGroups.SECRETARY) and \
                                      self.request.user in board_attending
        rel_proposals = self.object.issue.proposals
        context['proposals'] = rel_proposals.object_access_control(
            user=self.request.user, community=self.community)
        return context


    def post(self, request, *args, **kwargs):
        """ Used to change a proposal status (accept/reject)
            or a proposal's property completed/not completed
        """
        p = self.get_object()
        v = request.POST.get('accepted', None)
        if v:
            v = int(v)
            if v not in [
                        p.statuses.ACCEPTED,
                        p.statuses.REJECTED,
                        p.statuses.IN_DISCUSSION
                        ]:
                return HttpResponseBadRequest("Bad value for accepted POST parameter")

            p.status = v
            p.save()
        return redirect(p)


class ProposalEditView(AjaxFormView, ProposalMixin, UpdateView):
    form_class = EditProposalForm

    reload_on_success = True

    def get_required_permission(self):
        o = self.get_object()
        return 'issues.editclosed_proposal' if o.decided_at_meeting else 'issues.edittask_proposal'

    def get_form_kwargs(self):
        d = super(ProposalEditView, self).get_form_kwargs()
        d['prefix'] = 'proposal'
        d['community'] = self.community
        return d


class ProposalEditTaskView(ProposalMixin, UpdateView):
    form_class = EditProposalTaskForm

    def get_queryset(self):
        return super(ProposalEditTaskView, self).get_queryset().filter(type=ProposalType.TASK)

    def get_required_permission(self):
        o = self.get_object()
        return 'issues.editclosed_proposal' if o.decided_at_meeting else 'issues.editopen_proposal'


class ProposalCompletedTaskView(ProposalMixin, UpdateView):
    """ update a task as completed / un-completed
    """

    def post(self, request, *args, **kwargs):
        if not self._can_complete_task():
            return HttpResponseForbidden("403 Unauthorized")
        p = self.get_object()
        completed = request.POST.get('completed', None)
        if completed:
            p.task_completed = completed == '1'
            p.save()
            return redirect(p)


class ProposalDeleteView(AjaxFormView, ProposalMixin, DeleteView):

    def get_required_permission(self):
        o = self.get_object()
        if o.decided_at_meeting:
            return 'issues.editclosed_issue'

        return 'issues.add_proposal' if o.created_by == self.request.user \
            else 'issues.editopen_proposal'

    def get_success_url(self):
        return "" if self.issue.active else "-"

    def delete(self, request, *args, **kwargs):
        o = self.get_object()
        o.active = False
        o.save()
        return HttpResponse("-")


class VoteResultsView(CommunityMixin, DetailView):
    model = models.Proposal

    def get(self, request, *args, **kwargs):
        meeting = None
        meeting_id = request.GET.get('meeting_id', None)
        p = self.get_object()
        if meeting_id:
            meeting = get_object_or_404(Meeting, id=int(meeting_id))
            res = p.get_straw_results(meeting.id)
        else:
            meeting = self.community.draft_meeting()
            res = p.get_straw_results()

        panel = render_to_string('issues/_proposal_vote_results.html',
                                RequestContext(request, {
                                        'meeting': meeting,
                                        'res': res,
                                        'proposal': p,
                                        }))
        return HttpResponse(panel)


class ProposalVoteMixin(CommunityMixin):
    VOTE_OK = 0
    VOTE_VER_ERR = 1
    VOTE_OVERRIDE_ERR = 2

    def _do_vote(self, vote_class, proposal, user_id, value, is_board, voter_group):
        if is_board:
            # verify
            if not voter_group or voter_group == DefaultGroups.MEMBER \
               or proposal.decided:
                return (None, self.VOTE_VER_ERR)

        by_chairman = voter_group == DefaultGroups.CHAIRMAN
        vote, created = vote_class.objects.get_or_create(proposal_id=proposal.id,
                                                         user_id=user_id)
        if not created and by_chairman and not vote.voted_by_chairman:
            # don't allow chairman vote override a board member existing vote!
            return (vote, self.VOTE_OVERRIDE_ERR)
        vote.value=value
        if is_board:
            vote.voted_by_chairman = by_chairman
        vote.save()
        return (vote, self.VOTE_OK)

    def _vote_values_map(self, key):
        vote_map = {
            'pro': 1,
            'con': -1,
            'neut': 0,
            'reset': -2,
        }
        if type(key) != int:
            try:
                return vote_map[key]
            except KeyError:
                return None
        else:
            for k, val in vote_map.items():
                if key == val:
                    return k
        return None

class ProposalVoteView(ProposalVoteMixin, DetailView):
    required_permission_for_post = 'issues.vote'
    model = models.Proposal

    def post(self, request, *args, **kwargs):

        is_board = request.POST.get('board', False)
        user_id = request.POST.get('user', request.user.id)
        voter_id = request.user.id
        voter_group = request.user.get_default_group(self.community) \
                if request.user.is_authenticated() \
                else ''
        val = request.POST['val']
        if is_board:
            # vote for board member by chairman or board member
            vote_class = ProposalVoteBoard
        else:
            # straw vote by member
            vote_class = ProposalVote

        proposal = self.get_object()
        pid = proposal.id
        vote_panel_tpl = 'issues/_vote_panel.html' if val == 'reset' \
                            else 'issues/_vote_reset_panel.html'

        res_panel_tpl = 'issues/_board_vote_res.html' if is_board \
                            else 'issues/_vote_reset_panel.html'
        vote_response = {
                'result': 'ok',
                'html': render_to_string(res_panel_tpl,
                    {
                        'proposal': proposal,
                        'community': self.community,
                    }),
        }

        value = ''
        if val == 'reset':
            vote = get_object_or_404(vote_class,
                                     proposal_id=pid, user_id=user_id)
            vote.delete()
            vote_response['html'] = render_to_string(vote_panel_tpl,
                    {
                        'proposal': proposal,
                        'community': self.community,
                    })

            return json_response(vote_response)
        else:
            value = self._vote_values_map(val)
        if value == None:
            return HttpResponseBadRequest('vote value not valid')

        vote, valid = self._do_vote(vote_class, proposal, user_id, value,
                                    is_board, voter_group)
        if valid == ProposalVoteMixin.VOTE_OK:
            vote_response['html'] = render_to_string(res_panel_tpl,
                    {
                        'proposal': proposal,
                        'community': self.community,
                    })
            if is_board and voter_group == DefaultGroups.CHAIRMAN:
                vote_response['sum'] = render_to_string('issues/_member_vote_sum.html',
                        {
                            'proposal': proposal,
                            'community': self.community,
                            'board_attending': board_voters_on_proposal(proposal),
                        })
        else:
            vote_response['result'] = 'err'
            if valid == ProposalVoteMixin.VOTE_OVERRIDE_ERR:
                vote_response['override_fail'] = [{'uid': user_id,
                                               'val': self._vote_values_map(vote.value),
                                             }]

        return json_response(vote_response)


class MultiProposalVoteView(ProposalVoteMixin, DetailView):
    required_permission_for_post = 'issues.chairman_vote'
    model = models.Proposal

    def post(self, request, *args, **kwargs):
        voted_ids = json.loads(request.POST['users'])
        proposal = self.get_object()
        pid = proposal.id
        voter_group = request.user.get_default_group(self.community) \
                if request.user.is_authenticated() \
                else ''

        val = request.POST['val']

        value = self._vote_values_map(val)
        if value == None:
            return HttpResponseBadRequest('vote value not valid')

        vote_failed = []
        for user_id in voted_ids:
            vote, valid = self._do_vote(ProposalVoteBoard, proposal,
                                       user_id, value, True, voter_group)
            if valid == ProposalVoteMixin.VOTE_OVERRIDE_ERR:
                vote_failed.append({'uid': user_id, 'val': self._vote_values_map(vote.value), })

        return json_response({
            'result': 'ok',
            'html': render_to_string('issues/_vote_reset_panel.html',
                                {
                                      'proposal': proposal,
                                      'community': self.community,
                                  }),
            'override_fail': vote_failed,
            'sum': render_to_string('issues/_member_vote_sum.html',
                {
                    'proposal': proposal,
                    'community': self.community,
                    'board_attending': board_voters_on_proposal(proposal),
                })
        })


class ChangeBoardVoteStatusView(ProposalMixin, UpdateView):
    required_permission_for_post = 'issues.chairman_vote'
    model = models.Proposal

    def post(self, request, *args, **kwargs):
        p = self.get_object()
        if request.POST.get('val', None):
            p.register_board_votes = request.POST.get('val') == '1'
            p.save()
            return json_response({'result': 'ok'})
        else:
            return json_response({'result': 'err'})


class AssignmentsView(ProposalMixin, ListView):
    required_permission = 'issues.viewopen_issue'
    template_name = 'issues/assignment_list.html'
    paginate_by = 75

    def __init__(self, **kwargs):
        super(AssignmentsView, self).__init__(**kwargs)
        self.status = ''

    def _get_order(self):
        order_by = self.request.GET.get('ord', 'date')
        if order_by == 'date':
            order_by = '-due_by'
        return order_by

    def _add_status_qs(self, sqs):
        self.status = self.request.GET.get('status', '')
        if self.status:
            if self.status == 'completed':
                sqs = sqs.filter(task_completed=True)
            else:
                sqs = sqs.filter(task_completed=False)
                if self.status == 'opened':
                    sqs = sqs.exclude(due_by__lt=date.today())
                elif self.status == 'late':
                    sqs = sqs.filter(due_by__lt=date.today())
        return sqs

    def get_queryset(self):
        term = self.request.GET.get('q', '').strip()
        sqs = ConfidentialSearchQuerySet().models(Proposal).object_access_control(
            user=self.request.user, community=self.community).filter(
            active=True, community=self.community.id,
            status=Proposal.statuses.ACCEPTED,
            type=ProposalType.TASK).order_by(self._get_order())
        sqs = self._add_status_qs(sqs)
        if term:
            sqs = sqs.filter(content=AutoQuery(term)) \
                     .filter_or(assignee__contains=term)
        return sqs.load_all()


    def get_context_data(self, **kwargs):
        d = super(AssignmentsView, self).get_context_data(**kwargs)
        search_query = self.request.GET.get('q', '').strip()
        d['late'] = [p.id for p in list(self.get_queryset()) \
                        if not p.object.task_completed and p.due_by \
                        and p.due_by.date() < date.today()]
        d['query'] = search_query
        d['ord'] = self._get_order()
        d['status'] = self.status
        d['filter_as_link'] = d['is_paginated'] or d['status']
        d['extra_arg'] = '&ord=' + d['ord'] + '&q=' + d['query'] + '&status=' + self.status
        return d


class RulesMixin(CommunityMixin):

    def _get_rule_queryset(self):
        qs = Proposal.objects.object_access_control(user=self.request.user,
            community=self.community).filter(
            active=True, issue__community=self.community,
            status=Proposal.statuses.ACCEPTED,
            type=ProposalType.RULE)
        return qs


class ProceduresView(RulesMixin, ProposalMixin, ListView):
    required_permission = 'issues.viewopen_issue'
    template_name = 'issues/procedure_list.html'
    context_object_name = 'procedure_list'
    paginate_by = 75

    def __init__(self, **kwargs):
        self.order_by = 'date'
        super(ProceduresView, self).__init__(**kwargs)

    def get_queryset(self):
        term = self.request.GET.get('q', '').strip()
        if not term:
            # try search by tag
            term = self.request.GET.get('t', '').strip()
        self.order_by = self.request.GET.get('ord', 'date')
        ord_term = '-decided_at' if self.order_by == 'date' else 'title'
        sqs = ConfidentialSearchQuerySet().object_access_control(
            user=self.request.user, community=self.community).filter(
            active=True, community=self.community.id,
            status=Proposal.statuses.ACCEPTED,
            type=ProposalType.RULE).order_by(ord_term)
        if term:
            sqs = sqs.filter(content=AutoQuery(term))
        return sqs.load_all()

    def get_context_data(self, **kwargs):
        def _sort_by_popularity(a, b):
            return cmp(a[1], b[1])

        d = super(ProceduresView, self).get_context_data(**kwargs)
        alltags = {}
        for p in self._get_rule_queryset():
            for t in p.tags.names():
                n = alltags.setdefault(t, 0)
                alltags[t] = n + 1
        sorted_tags = sorted(alltags.items(), _sort_by_popularity, reverse=True)
        search_query = self.request.GET.get('q', '').strip()
        tag_query = self.request.GET.get('t', '').strip()
        d['sorted_tags'] = sorted_tags
        d['query'] = search_query or tag_query
        d['extra_arg'] = '&ord=' + self.order_by + '&q=' + d['query']
        d['ord'] = self.order_by
        d['active_tag'] = tag_query
        d['tags_as_links'] = (not search_query and d['is_paginated']) or len(d['object_list']) == 0
        return d


class AutoCompleteTagView(CommunityMixin, View):
    required_permission = 'issues.editopen_issue'

    def get(self, request, *args, **kwargs):
          tag = request.GET.get('tag', '')
          tag = tag.split(',')[-1].strip()
          print 'T: ', tag
          if not tag:
              return HttpResponse(json.dumps([]))
          json_tags = []
          tags = set()
          proposals = Proposal.objects.filter(
            active=True, issue__community=self.community,
            type=ProposalType.RULE)
          for p in proposals:
              tags.update(t for t in p.tags.names() if t.startswith(tag))
          for t in tags:
              json_tags.append({'tokens':[t,], 'value': t})

          # context = self.get_context_data(object_list=proposals)
          return HttpResponse(json.dumps(json_tags), {'content_type': 'application/json'})

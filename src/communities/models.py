import logging
from django.conf import settings
from django.db import models, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import ugettext, ugettext_lazy as _
from issues.models import ProposalStatus, IssueStatus, VoteResult
from meetings.models import MeetingParticipant, Meeting
from ocd.base_models import HTMLField, UIDMixin
from users.default_roles import DefaultGroups
from users.models import OCUser, Membership
import issues.models as issues_models
import meetings.models as meetings_models


logger = logging.getLogger(__name__)


class SendToOption(object):
    ONLY_ME = 1
    ONLY_ATTENDEES = 2
    BOARD_ONLY = 3
    ALL_MEMBERS = 4

    choices = (
        (ONLY_ME, _("Me only (review)")),
        (ONLY_ATTENDEES, _("Only attendees")),
        (BOARD_ONLY, _("The board")),
        (ALL_MEMBERS, _("All members")),
    )

    publish_choices = (
        (ONLY_ME, _("Me only (review)")),
        (BOARD_ONLY, _("The board")),
        (ALL_MEMBERS, _("All members")),
    )


class Community(UIDMixin):
    name = models.CharField(max_length=200, verbose_name=_("Name"))
    is_public = models.BooleanField(_("Public community"), default=False,
                                    db_index=True)
    logo = models.ImageField(_("Community logo"), upload_to='community_logo',
                             blank=True, null=True)
    official_identifier = models.CharField(_("Community identifier"),
                                           max_length=300, blank=True,
                                           null=True)

    upcoming_meeting_started = models.BooleanField(_("Meeting started"),
                                                   default=False)
    upcoming_meeting_title = models.CharField(_("Upcoming meeting title"),
                                              max_length=300, null=True,
                                              blank=True)
    upcoming_meeting_scheduled_at = models.DateTimeField(
        _("Upcoming meeting scheduled at"), blank=True, null=True)
    upcoming_meeting_location = models.CharField(
        _("Upcoming meeting location"), max_length=300, null=True, blank=True)
    upcoming_meeting_comments = HTMLField(_("Upcoming meeting background"),
                                          null=True, blank=True)

    upcoming_meeting_participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="+",
        verbose_name=_("Participants in upcoming meeting"))

    upcoming_meeting_guests = models.TextField(
        _("Guests in upcoming meeting"), null=True, blank=True,
        help_text=_("Enter each guest in a separate line"))

    upcoming_meeting_version = models.IntegerField(
        _("Upcoming meeting version"), default=0)

    upcoming_meeting_is_published = models.BooleanField(
        _("Upcoming meeting is published"), default=False)

    upcoming_meeting_published_at = models.DateTimeField(
        _("Upcoming meeting published at"), blank=True, null=True)

    upcoming_meeting_summary = HTMLField(_("Upcoming meeting summary"),
                                         null=True, blank=True)

    board_name = models.CharField(_("Board name"), default=_("Board"),
                                  max_length=200)

    straw_voting_enabled = models.BooleanField(_("Straw voting enabled"),
                                               default=False)

    issue_ranking_enabled = models.BooleanField(
        _("Issue ranking votes enabled"), default=False)

    voting_ends_at = models.DateTimeField(_("Straw Vote ends at"), null=True,
                                          blank=True)

    referendum_started = models.BooleanField(_("Referendum started"),
                                             default=False)

    referendum_started_at = models.DateTimeField(_("Referendum started at"),
                                                 null=True, blank=True)

    referendum_ends_at = models.DateTimeField(_("Referendum ends at"),
                                              null=True, blank=True)

    default_quorum = models.PositiveSmallIntegerField(_("Default quorum"),
                                                      null=True, blank=True)

    allow_links_in_emails = models.BooleanField(
        _("Allow links inside emails"), default=True)

    email_invitees = models.BooleanField(_("Send mails to invitees"),
                                         default=False)

    register_missing_board_members = models.BooleanField(
        _("Register missing board members"), default=False)

    inform_system_manager = models.BooleanField(
        _('Inform System Manager'), default=False)

    class Meta:
        verbose_name = _("Community")
        verbose_name_plural = _("Communities")

    def __unicode__(self):
        return self.name

    @models.permalink
    def get_absolute_url(self):
        return "community", (str(self.pk),)

    @models.permalink
    def get_upcoming_absolute_url(self):
        return "community", (str(self.pk),)

    def upcoming_issues(self, user=None, upcoming=True):
        l = issues_models.IssueStatus.IS_UPCOMING if upcoming else \
            issues_models.IssueStatus.NOT_IS_UPCOMING

        if self.issues.all():
            rv = self.issues.object_access_control(
                user=user, community=self).filter(
                active=True, status__in=(l)).order_by(
                'order_in_upcoming_meeting')
        else:
            rv = None
        return rv

    def available_issues(self, user=None):
        if self.issues.all():
            rv = self.issues.object_access_control(
                user=user, community=self).filter(
                active=True, status=issues_models.IssueStatus.OPEN).order_by(
                '-created_at')
        else:
            rv = None
        return rv

    def available_issues_by_rank(self):
        return self.issues.filter(active=True,
                                  status=issues_models.IssueStatus.OPEN
        ).order_by('order_by_votes')

    def issues_ready_to_close(self, user=None, community=None):
        if self.upcoming_issues(user=user):
            rv = self.upcoming_issues(user=user).filter(
            proposals__active=True,
            proposals__decided_at_meeting=None,
            proposals__status__in=[
                ProposalStatus.ACCEPTED,
                ProposalStatus.REJECTED
            ]
        )
        else:
            rv = None
        return rv

    def get_board_name(self):
        return self.board_name or _('Board')

    def get_members(self):
        return OCUser.objects.filter(memberships__community=self)

    def meeting_participants(self):

        meeting_participants = {'chairmen': [], 'board': [], 'members': [], }

        board_ids = [m.user.id for m in self.memberships.board()]

        for u in self.upcoming_meeting_participants.all():
            if u.id in board_ids:
                if u.get_default_group(self) == DefaultGroups.CHAIRMAN:
                    meeting_participants['chairmen'].append(u)
                else:
                    meeting_participants['board'].append(u)
            else:
                meeting_participants['members'].append(u)

        # doing it simply like this, as I'd need to refactor models
        # just to order in the way that is now required.
        for index, item in enumerate(meeting_participants['board']):
            if item.get_default_group(self) == DefaultGroups.MEMBER:
                meeting_participants['board'].insert(0,
                    meeting_participants['board'].pop(index))

        return meeting_participants

    def previous_members_participations(self):
        participations = MeetingParticipant.objects.filter( \
            default_group_name=DefaultGroups.MEMBER,
            meeting__community=self) \
            .order_by('-meeting__held_at')

        return list(set([p.user for p in participations]) - \
                    set(self.upcoming_meeting_participants.all()))

    def previous_guests_participations(self):
        guests_list = Meeting.objects.filter(community=self) \
            .values_list('guests', flat=True)

        prev_guests = set()
        upcoming_guests = self.upcoming_meeting_guests or ' '
        for guest in guests_list:
            if guest:
                prev_guests.update(guest.splitlines())
        prev_guests.difference_update(upcoming_guests.splitlines())
        return prev_guests

    def get_board_members(self):
        board_memberships = Membership.objects.filter(community=self) \
            .exclude(default_group_name=DefaultGroups.MEMBER)

        # doing it simply like this, as I'd need to refactor models
        # just to order in the way that is now required.
        board = [m.user for m in board_memberships]
        for index, item in enumerate(board):
            if item.get_default_group(self) == DefaultGroups.MEMBER:
                board.insert(0, board.pop(index))

        return board

    def get_board_count(self):
        return len(self.get_board_members())

    def get_none_board_members(self):
        return Membership.objects.filter(community=self,
                                         default_group_name=DefaultGroups.MEMBER)

    def get_guest_list(self):
        if not self.upcoming_meeting_guests:
            return []
        return filter(None, [s.strip() for s in
                             self.upcoming_meeting_guests.splitlines()])

    def full_participants(self):
        guests_count = len(self.upcoming_meeting_guests.splitlines()) \
            if self.upcoming_meeting_guests else 0
        return guests_count + self.upcoming_meeting_participants.count()

    @property
    def straw_vote_ended(self):
        if not self.upcoming_meeting_is_published:
            return True
        if not self.voting_ends_at:
            return False
        time_till_close = self.voting_ends_at - timezone.now()
        return time_till_close.total_seconds() < 0

    def has_straw_votes(self, user=None, community=None):
        if not self.straw_voting_enabled or self.straw_vote_ended:
            return False
        return self.upcoming_proposals_any({'is_open': True}, user=user,
                                           community=community)

    def sum_vote_results(self, only_when_over=True):
        if not self.voting_ends_at:
            return
        time_till_close = self.voting_ends_at - timezone.now()
        if only_when_over and time_till_close.total_seconds() > 0:
            return

        proposals_to_sum = issues_models.Proposal.objects.filter(
            # votes_pro=None,
            status=ProposalStatus.IN_DISCUSSION,
            issue__status=IssueStatus.IN_UPCOMING_MEETING,
            issue__community_id=self.id)
        member_count = self.get_members().count()
        for prop in proposals_to_sum:
            prop.do_votes_summation(member_count)

    def _get_upcoming_proposals(self, user=None, community=None):
        proposals = []
        upcoming = self.upcoming_issues(user=user)
        if upcoming:
            for issue in upcoming:
                if issue.proposals.all():
                    proposals.extend([p for p in issue.proposals.all() if p.active])
        return proposals

    def upcoming_proposals_any(self, prop_dict, user=None, community=None):
        """ test multiple properties against proposals belonging to the upcoming meeting
            return True if any of the proposals passes the tests
        """
        proposals = self._get_upcoming_proposals(user=user, community=community)
        test_attrs = lambda p: [getattr(p, k) == val for k, val in
                                prop_dict.items()]
        for p in proposals:
            if all(test_attrs(p)):
                return True
        return False

    def _register_absents(self, meeting, meeting_participants):
        board_members = [mm.user for mm in Membership.objects.board() \
            .filter(community=self, user__is_active=True)]
        absents = set(board_members) - set(meeting_participants)
        ordinal_base = len(meeting_participants)
        for i, a in enumerate(absents):
            try:
                mm = a.memberships.get(community=self)
            except Membership.DoesNotExist:
                mm = None
            MeetingParticipant.objects.create(meeting=meeting, user=a,
                                              display_name=a.display_name,
                                              ordinal=ordinal_base + i,
                                              is_absent=True,
                                              default_group_name=mm.default_group_name if mm else None)

    def close_meeting(self, m, user, community):
        """
        Creates a :model:`meetings.Meeting` instance, with corresponding
        :model:`meetings.AgenddItem`s.

        Optionally changes statuses for :model:`issues.Issue`s and
        :model:`issues.Proposal`s.
        """

        with transaction.commit_on_success():
            m.community = self
            m.created_by = user
            m.title = self.upcoming_meeting_title
            m.scheduled_at = (self.upcoming_meeting_scheduled_at
                              or timezone.now())
            m.location = self.upcoming_meeting_location
            m.comments = self.upcoming_meeting_comments
            m.guests = self.upcoming_meeting_guests
            m.summary = self.upcoming_meeting_summary

            m.save()

            self.upcoming_meeting_started = False
            self.upcoming_meeting_title = None
            self.upcoming_meeting_scheduled_at = None
            self.upcoming_meeting_location = None
            self.upcoming_meeting_comments = None
            self.upcoming_meeting_summary = None
            self.upcoming_meeting_version = 0
            self.upcoming_meeting_is_published = False
            self.upcoming_meeting_published_at = None
            self.upcoming_meeting_guests = None
            self.voting_ends_at = None
            self.save()

            for i, issue in enumerate(self.upcoming_issues(user=user)):

                proposals = issue.proposals.filter(
                    active=True,
                    decided_at_meeting=None
                ).exclude(
                    status=ProposalStatus.IN_DISCUSSION
                )
                for p in proposals:
                    p.decided_at_meeting = m
                    p.save()

                for p in issue.proposals.all():
                    if p.votes_pro is not None:
                        try:
                            VoteResult.objects.create(
                                proposal=p,
                                meeting=m,
                                votes_pro=p.votes_pro,
                                votes_con=p.votes_con,
                                community_members=p.community_members)
                        except:
                            pass

                for c in issue.comments.filter(meeting=None):
                    c.meeting = m
                    c.save()

                ai = meetings_models.AgendaItem.objects.create(
                    meeting=m, issue=issue, order=i,
                    background=issue.abstract,
                    closed=issue.completed)

                issue.attachments.filter(active=True,
                                         agenda_item__isnull=True) \
                    .update(agenda_item=ai)
                issue.is_published = True
                issue.abstract = None

                if issue.completed:
                    issue.order_in_upcoming_meeting = None

                issue.save()
            meeting_participants = self.upcoming_meeting_participants.all()
            for i, p in enumerate(meeting_participants):
                try:
                    mm = p.memberships.get(community=self)
                except Membership.DoesNotExist:
                    mm = None

                MeetingParticipant.objects.create(meeting=m, ordinal=i,
                                                  user=p,
                                                  display_name=p.display_name,
                                                  default_group_name=mm.default_group_name if mm else None)

            self._register_absents(m, meeting_participants)
            self.upcoming_meeting_participants = []

        return m

    def draft_meeting(self):
        if self.upcoming_meeting_scheduled_at:
            held_at = self.upcoming_meeting_scheduled_at.date()
        else:
            held_at = None

        return {
            'id': '',
            'held_at': held_at,
        }

    def draft_agenda(self, payload):
        """ prepares a fake agenda item list for 'protocol_draft' template. """

        # payload should be a list of dicts. Each dict has these keys:
        #   * issue
        #   * proposals
        #
        # The values are querysets

        def as_agenda_item(obj):
            return {
                'issue': obj['issue'],
                'proposals': obj['proposals'].filter(
                    decided_at_meeting=None,
                    active=True).exclude(
                    status=ProposalStatus.IN_DISCUSSION),
                'accepted_proposals': obj['proposals'].filter(
                    decided_at_meeting=None,
                    active=True,
                    status=ProposalStatus.ACCEPTED),
                'rejected_proposals': obj['proposals'].filter(
                    decided_at_meeting=None,
                    active=True,
                    status=ProposalStatus.REJECTED),
                'comments': obj['issue'].comments.filter(
                    meeting=None, active=True),
                'attachments': obj['issue'].current_attachments()
            }

        return [as_agenda_item(x) for x in payload]


class CommunityConfidentialReason(models.Model):

    """The set of reasons a community can declare an object confidential.

    A default set of reasons is populated when a community is created.

    This default set can be extended or modified.

    """

    class Meta:
        ordering = ['community']
        verbose_name = _('Confidential Reason')
        verbose_name_plural = _('Confidential Reasons')
        unique_together = (('community', 'title'),)

    community = models.ForeignKey(
        Community,
        related_name='confidential_reasons',
        help_text=_('A reason that can be used for marking items as '
                    'confidential in your community.'),)

    title = models.CharField(
        _('Name'),
        max_length=255,
        help_text=_('The title to give this reason.'),)

    def __unicode__(self):
        return self.title


@receiver(post_save, sender=Community)
def set_default_confidental_reasons(sender, instance, created,
                                    dispatch_uid='set_default_confidental_reasons',
                                    **kwargs):
    if created:
        for reason in settings.OPENCOMMUNITY_DEFAULT_CONFIDENTIAL_REASONS:
            CommunityConfidentialReason.objects.create(community=instance,
                                                       title=ugettext(reason))

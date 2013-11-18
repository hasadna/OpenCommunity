from django.conf import settings
from django.db import models, transaction
from django.db.models.query_utils import Q
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from issues.models import ProposalStatus, IssueStatus,Issue
from meetings.models import MeetingParticipant
from ocd.base_models import HTMLField, UIDMixin
from ocd.email import send_mails
from users.default_roles import DefaultGroups
from users.models import OCUser, Membership
import issues.models as issues_models
import logging
import meetings.models as meetings_models


logger = logging.getLogger(__name__)


class SendToOption(object):

    ONLY_ME = 1
    ONLY_ATTENDEES = 2
    BOARD_ONLY = 3
    ALL_MEMBERS = 4

    choices = (
               (ONLY_ME, _("Only Me (review)")),
               (ONLY_ATTENDEES, _("Only attendees")),
               (BOARD_ONLY, _("The Board")),
               (ALL_MEMBERS, _("All Members")),
              )

    publish_choices = (
               (ONLY_ME, _("Only Me (review)")),
               (BOARD_ONLY, _("The Board")),
               (ALL_MEMBERS, _("All Members")),
              )

              
class Community(UIDMixin):

    name = models.CharField(max_length=200, verbose_name=_("Name"))
    is_public = models.BooleanField(_("Public Community"), default=False,
                                    db_index=True)
    logo = models.ImageField(upload_to='community_logo', verbose_name=_("Community Logo"), blank=True, null=True)
    official_identifier = models.CharField(max_length=300, verbose_name=_("Community Identifier"), blank=True, null=True)

    upcoming_meeting_started = models.BooleanField(
                                        _("Meeting started"),
                                        default=False)
    upcoming_meeting_title = models.CharField(
                                         _("Upcoming meeting title"),
                                         max_length=300, null=True,
                                         blank=True)
    upcoming_meeting_scheduled_at = models.DateTimeField(
                                        _("Upcoming meeting scheduled at"),
                                        blank=True, null=True)
    upcoming_meeting_location = models.CharField(
                                         _("Upcoming meeting location"),
                                         max_length=300, null=True,
                                         blank=True)
    upcoming_meeting_comments = HTMLField(_("Upcoming meeting background"),
                                          null=True, blank=True)

    upcoming_meeting_participants = models.ManyToManyField(
                                      settings.AUTH_USER_MODEL,
                                      blank=True,
                                      related_name="+",
                                      verbose_name=_(
                                         "Participants in upcoming meeting"))

    upcoming_meeting_guests = models.TextField(_("Guests in upcoming meeting"),
                           null=True, blank=True,
                           help_text=_("Enter each guest in a separate line"))

    upcoming_meeting_version = models.IntegerField(
                                   _("Upcoming meeting version"), default=0)

    upcoming_meeting_is_published = models.BooleanField(
                                        _("Upcoming meeting is published"),
                                        default=False)
    upcoming_meeting_published_at = models.DateTimeField(
                                        _("Upcoming meeting published at"),
                                        blank=True, null=True)

    upcoming_meeting_summary = HTMLField(_("Upcoming meeting summary"),
                                         null=True, blank=True)
    board_name = models.CharField(_("Board Name"), max_length=200,
                                  null=True, blank=True)
                                  
    straw_voting_enabled = models.BooleanField(_("Straw voting enabled"),
                                        default=False)

    voting_ends_at = models.DateTimeField(_("Voting ends at"),
                                null=True, blank=True)

    referendum_started = models.BooleanField(_("Referendum started"),
                                            default=False)

    referendum_started_at = models.DateTimeField(_("Referendum started at"),
                                    null=True, blank=True)

    referendum_ends_at = models.DateTimeField(_("Referendum ends at"),
                                    null=True, blank=True)
                                    
    default_quorum = models.PositiveSmallIntegerField(_("Default quorum"),
                                    null=True, blank=True)


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

    def upcoming_issues(self, upcoming=True):
        l = issues_models.IssueStatus.IS_UPCOMING if upcoming else \
            issues_models.IssueStatus.NOT_IS_UPCOMING
        return self.issues.filter(active=True, status__in=(l)
                                  ).order_by('order_in_upcoming_meeting')

    def available_issues(self):
        return self.issues.filter(active=True, status=issues_models.IssueStatus.OPEN
                                  ).order_by('created_at')
    def issues_ready_to_close(self):
        return self.upcoming_issues().filter(
                                         proposals__active=True,
                                         proposals__decided_at_meeting=None,
                                         proposals__status__in=[
                                                    ProposalStatus.ACCEPTED,
                                                    ProposalStatus.REJECTED
                                                    ]
                                     )

    def get_board_name(self):
        return self.board_name or _('Board')

    def get_members(self):
        return OCUser.objects.filter(memberships__community=self)

    def get_guest_list(self):
        if not self.upcoming_meeting_guests:
            return []
        return filter(None, [s.strip() for s in self.upcoming_meeting_guests.splitlines()])

    def send_mail(self, template, sender, send_to, data=None, base_url=None):

        if not base_url:
            base_url = settings.HOST_URL

        d = data.copy() if data else {}

        d.update({
              'base_url': base_url,
              'community': self,
              'LANGUAGE_CODE': settings.LANGUAGE_CODE,
              'MEDIA_URL': settings.MEDIA_URL,
              'STATIC_URL': settings.STATIC_URL,
             })

        subject = render_to_string("emails/%s_title.txt" % template, d).strip()

        message = render_to_string("emails/%s.txt" % template, d)
        html_message = render_to_string("emails/%s.html" % template, d)
        from_email = "%s <%s>" % (self.name, settings.FROM_EMAIL)

        recipient_list = set([sender.email])

        if send_to == SendToOption.ALL_MEMBERS:
            recipient_list.update(list(
                      self.memberships.values_list('user__email', flat=True)))
        elif send_to == SendToOption.BOARD_ONLY:
            recipient_list.update(list(
                        self.memberships.board().values_list('user__email', flat=True)))
        elif send_to == SendToOption.ONLY_ATTENDEES:
            recipient_list.update(list(
                       self.upcoming_meeting_participants.values_list(
                                                          'email', flat=True)))

        logger.info("Sending agenda to %d users" % len(recipient_list))

        send_mails(from_email, recipient_list, subject, message, html_message)

        return len(recipient_list)

    def close_meeting(self, m, user):

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
            self.save()

            for i, issue in enumerate(self.upcoming_issues()):

                proposals = issue.proposals.filter(
                                active=True,
                                decided_at_meeting=None
                            ).exclude(
                                status=ProposalStatus.IN_DISCUSSION
                            )
                for p in proposals:
                    p.decided_at_meeting = m
                    p.save()

                for c in issue.comments.filter(active=True, meeting=None):
                    c.meeting = m
                    c.save()

                meetings_models.AgendaItem.objects.create(
                                              meeting=m, issue=issue, order=i,
                                              closed=issue.completed)

                issue.is_published = True

                if issue.completed:
                    issue.status = issue.statuses.ARCHIVED
                    issue.order_in_upcoming_meeting = None

                issue.save()

            for i, p in enumerate(self.upcoming_meeting_participants.all()):

                try:
                    mm = p.memberships.get(community=self)
                except Membership.DoesNotExist:
                    mm = None

                MeetingParticipant.objects.create(meeting=m, ordinal=i, user=p,
                      display_name=p.display_name,
                      default_group_name=mm.default_group_name if mm else None)

            self.upcoming_meeting_participants = []

        return m

    def draft_agenda(self):
        """ prepares a fake agenda item list for 'protocol_draft' template. """

        def as_agenda_item(issue):
            return {
                    'issue': issue,

                    'proposals':
                        issue.proposals.filter(decided_at_meeting=None,
                                               active=True)
                            .exclude(status=ProposalStatus.IN_DISCUSSION),
                            
                    'accepted_proposals':
                        issue.proposals.filter(decided_at_meeting=None,
                                               active=True,
                                               status=ProposalStatus.ACCEPTED),
                                               
                    'rejected_proposals':
                        issue.proposals.filter(decided_at_meeting=None,
                                               active=True,
                                               status=ProposalStatus.REJECTED),
                                               
                    'comments':
                        issue.comments.filter(meeting=None, active=True),
                    }

        return [as_agenda_item(x) for x in
                self.issues.filter(status__in=IssueStatus.IS_UPCOMING)]

class ContactUs(models.Model):
    contactName = models.CharField(max_length = 75, null = True, blank = True)
    community = models.ForeignKey(Community)
    issue = models.ForeignKey(Issue)
    email = models.EmailField(max_length = 75, null = True, blank = True)
    phone = models.CharField(max_length = 75, null = True, blank = True)
    message = models.CharField(max_length = 1000, null = True, blank = True)
    time = models.DateTimeField(auto_now_add = True)#have a time field only for model for ordering the messages
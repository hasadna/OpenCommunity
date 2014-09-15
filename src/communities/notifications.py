"""Services for sending notifications to community members."""
import logging
import datetime
from itertools import chain
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import translation
import django_rq
from communities.models import SendToOption
from users.default_roles import DefaultGroups
from issues.models import IssueStatus


logger = logging.getLogger(__name__)


def get_guests_emails(guests_text):
    guest_emails = []
    if guests_text:
        for line in guests_text.splitlines():
            if '[' in line:
                from_idx = line.find('[')
                to_idx = line.find(']', from_idx + 1)
                try:
                    guest_emails.append(line[from_idx+1:to_idx])
                except:
                    pass
    return guest_emails


def construct_mock_users(email_list, type):
    """Takes a list of email addresses and a user type, and returns a
    mock user object with just enough information to check for object
    access control.

    """

    class MockUser(object):
        def __init__(self, user_dict):
            for k, v in user_dict.items():
                setattr(self, k, v)

    users = []

    for email in email_list:
        user = {
            'email': email,
            'type': type,
            '_is_mock': True,
            'is_superuser': False
        }
        users.append(MockUser(user))

    return users


def _base_send_mail(community, notification_type, sender, send_to, data=None,
                    base_url=None, with_guests=False, language=None):
    """Sends mail to community members, and applies object access control.

    The type of email being sent is detected from notification_type.

    """

    if language:
        translation.activate(language)

    # before anything, we want to build our recipient list as email
    # will be personalized.

    if send_to == SendToOption.ONLY_ME:
        r = [sender]

    elif send_to == SendToOption.ALL_MEMBERS:
        r = [m.user for m in community.memberships.all()]

    elif send_to == SendToOption.BOARD_ONLY:
        r = [m.user for m in community.memberships.board()]

    elif send_to == SendToOption.ONLY_ATTENDEES:
        r = [user for user in community.upcoming_meeting_participants.all()]

    else:
        r = []
        logger.error('Received an email job with no valid send_to. '
                     'send_to: {0}.'.format(send_to))

    user_recipients = set(r)

    w = []

    if send_to != SendToOption.ONLY_ME:

        # Add guests to the watcher_recipients list if applicable
        if with_guests:
            guests_text = community.upcoming_meeting_guests
            guest_emails = get_guests_emails(guests_text)
            guests = construct_mock_users(guest_emails, 'guest')
            w.extend(guests)

        # Add system managers to the watcher_recipients list if applicable
        if community.inform_system_manager and \
           notification_type in ('agenda', 'protocol', 'protocol_draft'):
            manager_emails = [manager[1] for manager in settings.MANAGERS]
            managers = construct_mock_users(manager_emails, 'managers')
            w.extend(managers)

        # Add pending invitees to the watcher_recipients list if applicable
        if community.email_invitees:
            # pending invites to board only
            if send_to == SendToOption.BOARD_ONLY:
                invitees = [i for i in community.invitations.exclude(
                            default_group_name=DefaultGroups.MEMBER)]

            # All pending invites
            elif send_to == SendToOption.ALL_MEMBERS:
                invitees = [i for i in community.invitations.all()]

            w.extend(invitees)

    watcher_recipients = set(w)

    # Make a union of the two sets to create the actual recipient list
    recipients = user_recipients | watcher_recipients

    if not base_url:
        base_url = settings.HOST_URL

    d = data.copy() if data else {}

    d.update({
        'base_url': base_url,
        'community': community,
        'LANGUAGE_CODE': settings.LANGUAGE_CODE,
        'MEDIA_URL': settings.MEDIA_URL,
        'STATIC_URL': settings.STATIC_URL,
    })

    from_email = "%s <%s>" % (community.name, settings.FROM_EMAIL)

    for recipient in recipients:

        # TODO: All this logic for populating the context is basically copied
        # from the same code in the views. This is not ideal, but without
        # doing a refactor over great parts of the system it seems reasonable.

        if notification_type == 'protocol_draft':

            meeting_time = community.upcoming_meeting_scheduled_at
            if not meeting_time:
                meeting_time = datetime.datetime.now()

            draft_agenda_payload = []
            issue_status = IssueStatus.IS_UPCOMING
            issues = community.issues.object_access_control(
                    user=recipient, community=community).filter(
                    active=True, status__in=(issue_status)).order_by(
                    'order_in_upcoming_meeting')

            for issue in issues:
                proposals = issue.proposals.object_access_control(
                    user=recipient, community=community)
                draft_agenda_payload.append({'issue': issue, 'proposals': proposals})

            agenda_items = community.draft_agenda(draft_agenda_payload)
            item_attachments = [item['issue'].current_attachments() for
                                item in agenda_items]

            d.update({
                'recipient': recipient,
                'meeting_time': meeting_time.replace(second=0),
                'agenda_items': agenda_items,
                'attachments': list(chain.from_iterable(item_attachments))
            })

        elif notification_type == 'protocol':

            agenda_items = d['meeting'].agenda.object_access_control(
                user=recipient, community=community).all()

            # restrict the proposals of each agenda item
            for ai in agenda_items:
                ai.accepted_proposals = ai.accepted_proposals(
                    user=recipient, community=community)
                ai.rejected_proposals = ai.rejected_proposals(
                    user=recipient, community=community)
                ai.proposals = ai.proposals(
                    user=recipient, community=community)

            d.update({
                'recipient': recipient,
                'agenda_items': agenda_items,
            })

        elif notification_type == 'agenda':

            can_straw_vote = community.upcoming_proposals_any(
                 {'is_open': True}, user=recipient, community=community)\
            and community.upcoming_meeting_is_published
            upcoming_issues = community.upcoming_issues(user=recipient)
            issues = []

            for i in upcoming_issues:
                proposals = i.proposals.object_access_control(
                    user=recipient, community=community)
                issues.append({'issue': i, 'proposals': proposals})

            d.update({
                'recipient': recipient,
                'can_straw_vote': can_straw_vote,
                'issue_container': issues
            })

        msg = {}
        msg['subject'] = render_to_string("emails/{0}_title.txt".format(
            notification_type), d).strip()
        msg['body'] = render_to_string("emails/{0}.txt".format(notification_type), d)
        as_html = render_to_string("emails/{0}.html".format(
            notification_type), d)
        msg['from_email'] = from_email
        msg['to'] = [recipient.email]
        msg = dict((k, v) for k, v in msg.iteritems() if v)
        message = EmailMultiAlternatives(**msg)
        message.attach_alternative(as_html, 'text/html')
        message.send()

    return len(recipients)


def _async_send_mail(*args, **kwargs):
    django_rq.get_queue(settings.QUEUE_NAME).enqueue(
        _base_send_mail, *args, description=u"Send mail",
        language=settings.LANGUAGE_CODE, **kwargs)
    return True


if not settings.OPENCOMMUNITY_ASYNC_NOTIFICATIONS:
    send_mail = _base_send_mail
else:
    send_mail = _async_send_mail

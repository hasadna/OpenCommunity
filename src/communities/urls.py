from communities import views
from django.conf.urls import patterns, url

urlpatterns = patterns('',

    url(r'^$', views.UpcomingMeetingView.as_view(), name='community'),

    url(r'^upcoming/publish/$', views.PublishUpcomingView.as_view(),
        name="upcoming_publish"),

    url(r'^upcoming/start/$', views.StartMeetingView.as_view(),
        name="upcoming_start"),

    url(r'^edit-summary/$', views.EditUpcomingSummaryView.as_view(),
        name="upcoming_edit_summary"),

    url(r'^upcoming/publish/preview/$',
        views.PublishUpcomingMeetingPreviewView.as_view(),
        name='preview_upcoming_meeting'),

    url(r'^upcoming/edit/$', views.EditUpcomingMeetingView.as_view(),
        name="upcoming_edit"),

    url(r'^upcoming/participants/$',
        views.EditUpcomingMeetingParticipantsView.as_view(),
        name="upcoming_edit_participants"),

    url(r'^protocol-preview/$',
        views.ProtocolDraftPreviewView.as_view(),
        name='preview_ongoing_protocol'),

    url(r'^contactus/$',
        views.ContactUsView.as_view(),
        name='contact_us'),
)

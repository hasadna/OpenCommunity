from django.contrib.auth.models import AnonymousUser
from django.db import models
from django.db.models.query import QuerySet
from haystack.query import SearchQuerySet
from users.default_roles import DefaultGroups


class ActiveQuerySetMixin(object):

    """Exposes methods that can be used on both the manager and the queryset.

    This allows us to chain custom methods.

    """

    def active(self):
        return self.get_query_set().filter(active=True)

GROUPS_ALLOWED_CONFIDENTIAL = frozenset([DefaultGroups.BOARD,
                                         DefaultGroups.CHAIRMAN,
                                         DefaultGroups.SECRETARY])

class ConfidentialQuerySetMixin(object):

    """Exposes methods that can be used on both the manager and the queryset.

    This allows us to chain custom methods.

    """

    def object_access_control(self, user, community):

        if not user:
            user = AnonymousUser()
        if not community:
            raise ValueError('The object access control method requires '
                             'a community object.')

        if user.is_superuser:
            return self.all()

        try:
            memberships = user.memberships
        except AttributeError:
            # No memberships -- not a proper user
            return self.filter(is_confidential=False)
        else:
            #TODO: integrate with permissions properly
            groups = (memberships.filter(community=community)
                                 .values_list('default_group_name', flat=True))

            if GROUPS_ALLOWED_CONFIDENTIAL & frozenset(groups):
                return self.all()
            else:
                return self.filter(is_confidential=False)


class ConfidentialQuerySet(QuerySet, ConfidentialQuerySetMixin):
    pass


class ConfidentialManager(models.Manager, ConfidentialQuerySetMixin):

    def get_query_set(self):
        return ConfidentialQuerySet(self.model, using=self._db)


class ConfidentialSearchQuerySet(SearchQuerySet):

    def object_access_control(self, user=None, community=None, **kwargs):
        if not user or not community:
            raise ValueError('The access validator requires both a user and '
                             'a community object.')
        qs = self._clone()
        if user.is_superuser:
            return qs
        elif user.is_anonymous():
            return qs.filter(is_confidential=False)
        else:
            memberships = user.memberships.filter(community=community)
            lookup = [m.default_group_name for m in memberships]
            if DefaultGroups.MEMBER in lookup and len(lookup) == 1:
                return qs.filter(is_confidential=False)
        return qs

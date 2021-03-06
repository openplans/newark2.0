from django.core import cache as django_cache
from twitter import Twitter, OAuth, TwitterHTTPError
from twitter.stream import TwitterStream
from urlparse import parse_qs
import re
from .models import AppConfig
from .cache import cache_buffer as cache
from .utils import chunk

from logging import getLogger
log = getLogger(__name__)


# ============================================================
# Exceptions
# ============================================================

class SocialMediaException (Exception):
    pass


# ============================================================
# The Twitter service
# ============================================================

class TwitterService (object):
    # ==================================================================
    # General Twitter info, cached
    # ==================================================================
    def get_config_cache_key(self):
        return 'twitter-config'

    def get_config(self, on_behalf_of=None):
        cache_key = self.get_config_cache_key()
        config = cache.get(cache_key)

        if config is None:
            t = self.get_api(on_behalf_of)
            config = t.help.configuration()
            config = dict(config.items())
            cache.set(cache_key, config)
        return config

    def get_url_length(self, url, on_behalf_of=None):
        config = self.get_config(on_behalf_of)
        if url.startswith('http://localhost') or url.startswith('http://127.0.0.1'):
            return len(url)

        if url.startswith('https'):
            return config['short_url_length_https']
        else:
            return config['short_url_length']

    # ==================================================================
    # User specific info, from Twitter, cached
    # ==================================================================
    def get_user_cache_key_prefix(self, user):
        return 'user-%s' % user.pk

    def get_user_cache_key(self, user, extra):
        return ':'.join([self.get_user_cache_key_prefix(user), extra])

    def get_user_info(self, user, on_behalf_of=None):
        cache_key = self.get_user_cache_key(user, 'info')
        info = cache.get(cache_key)

        if info is None:
            user_id = self.get_user_id(user)

            log_string = (
                '\n'
                '============================================================\n'
                'Hitting the API for %s to get info on %s (%s)\n'
                '============================================================\n'
            ) % (
                on_behalf_of.username if on_behalf_of else 'the app',
                user.username, user_id
            )
            log.info(log_string)

            t = self.get_api(on_behalf_of)
            try:
                info = t.users.show(user_id=user_id)
            except TwitterHTTPError:
                user.sm_not_found = True
                user.save(update_fields=['sm_not_found'])
                raise SocialMediaException('User %s (%s) not found on Twitter' % (user.username, user_id))
            info = dict(info.items())  # info is a WrappedTwitterResponse
            cache.set(cache_key, info)
        return info

    def get_users_info(self, users, on_behalf_of=None, force_refresh=False):
        # Build a mapping from cache_key => user_id
        data = {}
        for user in users:
            try:
                cache_key = self.get_user_cache_key(user, 'info')
                user_id = self.get_user_id(user)
                data[cache_key] = user_id
            except SocialMediaException as e:
                log.warning(e)
                pass

        # Build a reverse mapping from user_id => cache_key
        reverse_data = dict([
            (user_id, cache_key)
            for cache_key, user_id in data.items()
        ])

        # Get all the user info that is currently cached for the given users.
        # Assume all of the cache could be dirty with force_refresh.
        # NOTE: With too many users, this could become a problem, as we'll hit
        #       API limits with twitter. If there are more than 6000 users,
        #       just ignore the force_refresh and only update uncached users.
        if not force_refresh or len(data) > 6000:
            all_info = cache.get_many(data.keys())
        else:
            all_info = {}

        # Build a list of keys that have no cached data
        uncached_keys = filter(lambda key: key not in all_info, data.keys())

        if uncached_keys:
            log_string = (
                '\n'
                '============================================================\n'
                'Hitting the API for %s to get info on %s user(s)\n'
                'IDs: %s\n'
                '============================================================\n'
            ) % (
                on_behalf_of.username if on_behalf_of else 'the app',
                len(uncached_keys),
                ','.join([str(data[k]) for k in uncached_keys])
            )
            log.info(log_string)

            # If there are uncached keys, fetch the user info for those users
            # in chunks of 100
            t = self.get_api(on_behalf_of)
            user_ids = [data[key] for key in uncached_keys]
            new_info = {}
            questionable_ids = []

            for id_group in chunk(user_ids, 100):
                try:
                    bulk_info = t.users.lookup(user_id=','.join([str(user_id) for user_id in id_group]))
                except TwitterHTTPError:
                    bulk_info = []

                for info in bulk_info:
                    cache_key = reverse_data[str(info['id'])]
                    new_info[cache_key] = info

                seen_ids = set([info['id_str'] for info in bulk_info])
                questionable_ids += [user_id for user_id in id_group if user_id not in seen_ids]

            # If there were any IDs that we couldn't find, let someone know.
            if questionable_ids:
                user_strings = []
                for user_id in questionable_ids:
                    cache_key = reverse_data[str(user_id)]
                    user_id = data[cache_key]
                    user_strings.append(str(user_id))

                log_string = (
                    '\n'
                    '============================================================\n'
                    'The following user(s) are inaccessible from the Twitter API:\n'
                    '(i.e., they are protected, suspended, or gone)\n'
                    'IDs: %s\n'
                    '============================================================\n'
                ) % (
                    ','.join(user_strings)
                )
                log.warning(log_string)

            # Store any new information gotten in the cache
            cache.set_many(new_info)

            # Add the new info to the already cached info
            all_info.update(new_info)

            # Update the social media found status for all the users.
            from .models import User

            seen_users = User.objects.filter(social_auth__uid__in=seen_ids)
            seen_users.update(sm_not_found=False)

            questionable_users = User.objects.filter(social_auth__uid__in=questionable_ids)
            questionable_users.update(sm_not_found=True)

        return all_info.values()

    def get_avatar_url(self, user, on_behalf_of):
        user_info = self.get_user_info(user, on_behalf_of)
        url = user_info['profile_image_url']

        url_pattern = '^(?P<path>.*?)(?:_normal|_mini|_bigger|)(?P<ext>\.[^\.]*)$'
        match = re.match(url_pattern, url)
        if match:
            return match.group('path') + '_bigger' + match.group('ext')
        else:
            return url

    def get_full_name(self, user, on_behalf_of):
        user_info = self.get_user_info(user, on_behalf_of)
        return user_info['name']

    def get_bio(self, user, on_behalf_of):
        user_info = self.get_user_info(user, on_behalf_of)
        return user_info['description']

    def get_followed_users(self, user, on_behalf_of=None):
        cache_key = self.get_user_cache_key(user, 'follows')
        followed_user_ids = cache.get(cache_key)

        if followed_user_ids is None:
            user_id = self.get_user_id(user)

            log_string = (
                '\n'
                '============================================================\n'
                'Hitting the API for %s to get IDs for users that %s (%s)\n'
                'follows\n'
                '============================================================\n'
            ) % (
                on_behalf_of.username if on_behalf_of else 'the app',
                user.username, user_id
            )
            log.info(log_string)

            t = self.get_api(on_behalf_of)
            try:
                followed_user_ids = t.friends.ids(user_id=user_id)
            except TwitterHTTPError:
                user.sm_not_found = True
                user.save(update_fields=['sm_not_found'])
                raise SocialMediaException('User %s (%s) not found on Twitter' % (user.username, user_id))
            followed_user_ids = followed_user_ids['ids']
            cache.set(cache_key, followed_user_ids)
        return followed_user_ids

    # ==================================================================
    # User-specific info, from the database, used for authenticating
    # against Twitter on behalf of a specific user
    # ==================================================================
    def get_user_id(self, user):
        cache_key = self.get_user_cache_key(user, 'social-id')
        social_id = cache.get(cache_key)

        if social_id is None:
            try:
                # Assume the first one is the one we want
                social_auth = user.social_auth.all()[0]
            except IndexError:
                # If we don't have any, just return empty
                raise SocialMediaException(
                    'User %s is not authenticated with a social media account'
                    % (user,))

            if social_auth.provider == 'twitter':
                social_id = social_auth.uid
            else:
                raise SocialMediaException(
                    ('Can\'t get info for user %s authenticated with a %r '
                     'provider') % (user, social_auth.provider)
                )

            # Cache for just long enough to complete the current batch of
            # lookups without having to hit the DB again.
            cache.set(cache_key, social_id, 60)

        return social_id

    def get_user_oauth(self, user):
        cache_key = self.get_user_cache_key(user, 'oauth-args')
        oauth_args = cache.get(cache_key)

        if oauth_args is None:
            try:
                # Assume the first one is the one we want
                social_auth = list(user.social_auth.all())[0]
            except IndexError:
                # If we don't have any, just return empty
                raise SocialMediaException(
                    'User is not authenticated with a social media account')

            if social_auth.provider == 'twitter':
                extra_data = social_auth.extra_data
                access_token = parse_qs(extra_data['access_token'])
            else:
                raise SocialMediaException(
                    ('Can\'t get info for a user authenticated with a %r '
                     'provider') % social_auth.provider
                )

            app_config = AppConfig.get()
            oauth_args = (
                access_token['oauth_token'][0],
                access_token['oauth_token_secret'][0],
                app_config.twitter_consumer_key,
                app_config.twitter_consumer_secret,
            )
            # Cache for just long enough to complete the current batch of
            # lookups without having to hit the DB again.
            cache.set(cache_key, oauth_args, 60)

        return OAuth(*oauth_args)

    # ==================================================================
    # App-specific info, from the database, used for authenticating
    # against Twitter on behalf of the app
    # ==================================================================
    def get_app_oauth(self):
        app_config = AppConfig.get()
        return OAuth(
            app_config.twitter_access_token,
            app_config.twitter_access_token_secret,
            app_config.twitter_consumer_key,
            app_config.twitter_consumer_secret,
        )

    def get_api(self, on_behalf_of=None):
        # If user is None, tweet from the app's account
        if on_behalf_of is None:
            oauth = self.get_app_oauth()
        # Otherwise, tweet from the user's twitter account
        else:
            oauth = self.get_user_oauth(on_behalf_of)

        return Twitter(auth=oauth)

    def get_stream(self, on_behalf_of=None, **kwargs):
        # If user is None, tweet from the app's account
        if on_behalf_of is None:
            oauth = self.get_app_oauth()
        # Otherwise, tweet from the user's twitter account
        else:
            oauth = self.get_user_oauth(on_behalf_of)

        return TwitterStream(auth=oauth, **kwargs)

    # ==================================================================
    # Twitter actions
    # ==================================================================
    def tweet(self, text, on_behalf_of=None, **extra):
        t = self.get_api(on_behalf_of)
        try:
            result = t.statuses.update(status=text, **extra)
        except TwitterHTTPError as e:
            return False, e.response_data
        else:
            if on_behalf_of is not None:
                user_ids = cache.get('listening_user_ids', set())
                if self.get_user_id(on_behalf_of) not in user_ids:
                    # Since we're communicating with a different process, go
                    # directly to the central cache.
                    django_cache.cache.set('restart_listener', True)
            return True, result

    def add_favorite(self, on_behalf_of, tweet_id, **extra):
        t = self.get_api(on_behalf_of)
        try:
            return True, t.favorites.create(_id=tweet_id, **extra)
        except TwitterHTTPError as e:
            return False, e.response_data

    def remove_favorite(self, on_behalf_of, tweet_id, **extra):
        t = self.get_api(on_behalf_of)
        try:
            return True, t.favorites.destroy(_id=tweet_id, **extra)
        except TwitterHTTPError as e:
            return False, e.response_data

    def retweet(self, tweet_id, on_behalf_of, **extra):
        t = self.get_api(on_behalf_of)
        try:
            return True, t.statuses.retweet(id=tweet_id, **extra)
        except TwitterHTTPError as e:
            return False, e.response_data

    #
    # Streaming
    #
    def itertweets(self, on_behalf_of=None, **extra):
        s = self.get_stream(on_behalf_of, block=False)
        return s.statuses.filter(**extra)


default_twitter_service = TwitterService()

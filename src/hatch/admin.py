import django
from django.core.urlresolvers import reverse
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import (
    UserCreationForm as BaseUserCreationForm,
    UserChangeForm as BaseUserChangeForm,
)
from django.contrib import messages
from django.db.models import Q
from django.utils.html import format_html
import json
from .models import Vision, Reply, Share, User, Category, Tweet, AppConfig
from .views import VisionViewSet


class TweetAssignmentFilter(admin.SimpleListFilter):
    title = 'Assignment'
    parameter_name = 'assignment'

    def lookups(self, request, model_admin):
        return (
            ('visions', 'Visions'),
            ('replies', 'Replies'),
            ('null', 'Unassigned'),
        )

    def queryset(self, request, queryset):
        val = self.value()
        if val == 'visions':
            return queryset.filter(Q(user_tweeted_vision__isnull=False) | Q(app_tweeted_vision__isnull=False))
        if val == 'replies':
            return queryset.filter(reply__isnull=False)
        if val == 'null':
            return queryset.filter(user_tweeted_vision__isnull=True, app_tweeted_vision__isnull=True, reply__isnull=True)


class KnownReplyFilter(admin.SimpleListFilter):
    title = 'Is a Reply Tweet'
    parameter_name = 'is_a_reply'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Yes'),
            ('no', 'No'),
        )

    def queryset(self, request, queryset):
        val = self.value()
        if val == 'yes':
            return queryset.filter(in_reply_to__isnull=False)
        if val == 'no':
            return queryset.filter(in_reply_to__isnull=True)


class TweetAdmin (admin.ModelAdmin):
    actions = ('make_visions', 'make_replies',)
    date_hierarchy = 'created_at'
    list_display = ('__unicode__', 'tweeter', 'text', 'assignment', 'is_a_reply')
    list_filter = (TweetAssignmentFilter, KnownReplyFilter)
    raw_id_fields = ('in_reply_to',)
    readonly_fields = ('tweeter', 'text', 'original_tweet', 'tweet_in_reply_to', 'assignment')
    search_fields = ('tweet_data',)

    # Queryset
    def queryset(self, request):
        queryset = super(TweetAdmin, self).queryset(request)
        return queryset.select_related('vision', 'reply')

    # Read-only Fields
    def tweeter(self, tweet):
        user_data = tweet.tweet_data.get('user', {})
        return '%s (%s)' % (user_data.get('screen_name'), user_data.get('name'))

    def text(self, tweet):
        return tweet.tweet_data.get('text')
    text.allow_tags = True

    def original_tweet(self, tweet):
        if 'id' in tweet.tweet_data and 'user' in tweet.tweet_data:
            return ('on twitter... <a href="http://twitter.com/%(username)s/status/%(tweet_id)s">%(tweet_id)s</a>' % {'tweet_id': tweet.tweet_data['id'], 'username': tweet.tweet_data['user']['screen_name']})
    original_tweet.allow_tags = True  # Do not HTML-escape the value

    def tweet_in_reply_to(self, tweet):
        if tweet.tweet_data.get('in_reply_to_status_id'):
            return ('on twitter... <a href="http://twitter.com/%(username)s/status/%(tweet_id)s">%(tweet_id)s</a>' % {'tweet_id': tweet.tweet_data['in_reply_to_status_id'], 'username': tweet.tweet_data['user']['screen_name']})
    tweet_in_reply_to.allow_tags = True  # Do not HTML-escape the value

    def assignment(self, tweet):
        try:
            tweet.vision
            return ('<a href="%s">Vision %s</a>' % (reverse('admin:hatch_vision_change', args=[tweet.vision.id]), tweet.vision.id))
        except Vision.DoesNotExist:
            pass

        try:
            tweet.reply
            return ('<a href="%s">Reply %s</a>' % (reverse('admin:hatch_reply_change', args=[tweet.reply.id]), tweet.reply.id))
        except Reply.DoesNotExist:
            pass
    assignment.allow_tags = True  # Do not HTML-escape the value

    # Actions
    def is_a_reply(self, tweet):
        return tweet.in_reply_to_id is not None
    is_a_reply.boolean = True  # Display a "pretty" on/off icon

    def make_visions(self, request, tweet_qs):
        tweet_qs.make_visions()
        self.message_user(request, 'Successfully converted %s tweets to visions.' % (tweet_qs.count(),))
    make_visions.short_description = "Make visions from the selected tweets"

    def make_replies(self, request, tweet_qs):
        try:
            tweet_qs.make_replies()
            self.message_user(request, 'Successfully converted %s tweets to replies.' % (tweet_qs.count(),))
        except ValueError:

            # If we fail to make them all replies (which is fastest), go one
            # by one and count the failures.
            successes = 0
            failures = 0

            for tweet in tweet_qs.all():
                try:
                    tweet.make_reply()
                    successes += 1
                except ValueError:
                    failures += 1

            self.message_user(request, 'Successfully converted %s tweet(s) to replies. %s tweet(s) are not yet replies to visions. Assign tweets to be replies before proceeding.' % (successes, failures), level=messages.WARNING)
    make_replies.short_description = "Make replies from the selected tweets"


class ShareInline (admin.TabularInline):
    model = Share
    extra = 1
    raw_id_fields = ('user',)


class ReplyInline (admin.TabularInline):
    model = Reply
    extra = 3
    raw_id_fields = ('tweet', 'author',)


class VisionAdmin (admin.ModelAdmin):
    filter_horizontal = ('supporters',)
    inlines = [ReplyInline, ShareInline]
    list_display = ('__unicode__', 'author', 'text', 'category', 'featured')
    list_editable = ('category', 'featured',)
    list_filter = ('category', 'created_at', 'updated_at')
    raw_id_fields = ('tweet', 'author',)
    readonly_fields = ('tweet_text',)
    search_fields = ('text', 'category')

    def queryset(self, request):
        queryset = super(VisionAdmin, self).queryset(request)
        return queryset.select_related('category', 'author')

    def change_view(self, request, *args, **kwargs):
        # Save the request so that we can use it when
        # constructing the tweet text.
        self.request = request
        return super(VisionAdmin, self).change_view(request, *args, **kwargs)

    def tweet_text(self, vision):
        return VisionViewSet.get_app_tweet_text(self.request, vision)


class CategoryAdmin (admin.ModelAdmin):
    list_display = ('full_name', 'active')
    list_editable = ('active',)
    list_filter = ('active',)
    search_fields = ('name', 'title', 'prompt')

    def full_name(self, category):
        return '%s (%s)' % (category.title, category.name)

    def get_queryset(self, request):
        # Get the base queryset.
        if django.VERSION < (1, 6):
            qs = super(CategoryAdmin, self).queryset(request)
        else:
            qs = super(CategoryAdmin, self).get_queryset(request)

        # If this is not a simple GET request, just return the base queryset
        # immediately.
        if request.method.lower() != 'get':
            return qs

        # Otherwise, calculate the number of active categories and display a
        # message if necessary.
        total_count = qs.count()
        active_count = qs.filter(active=True).count()

        if total_count == 0:
            self.message_user(
                request, "You must have at least one category!",
                level=messages.ERROR)
        elif active_count == 0:
            self.message_user(
                request, "There are no active categories selected! You must "
                         "select an active category.",
                level=messages.ERROR)
        elif active_count > 1:
            self.message_user(
                request, "You have more than one category selected as active. "
                         "One of the categories will be arbitrarily chosen by "
                         "the app as active. To remove ambiguity, you should "
                         "always choose EXACTLY ONE category to be active at "
                         "a time.",
                level=messages.WARNING)

        return qs

    if django.VERSION < (1, 6):
        # https://docs.djangoproject.com/en/dev/ref/contrib/admin/#django.contrib.admin.ModelAdmin.get_queryset
        queryset = get_queryset


class ReplyAdmin (admin.ModelAdmin):
    raw_id_fields = ('tweet', 'vision', 'author')


class UserCreationForm (BaseUserCreationForm):
    class Meta:
        model = User
        fields = ("username",)

    # NOTE: We must override clean_username because of this bug:
    #       https://code.djangoproject.com/ticket/19353
    # TODO: Get rid of this whenever that patch gets merged in.
    def clean_username(self):
        # Since User.username is unique, this check is redundant,
        # but it sets a nicer error message than the ORM. See #13147.
        username = self.cleaned_data["username"]
        try:
            self._meta.model._default_manager.get(username=username)
        except self._meta.model.DoesNotExist:
            return username
        from django import forms
        raise forms.ValidationError(self.error_messages['duplicate_username'])


class UserChangeForm (BaseUserChangeForm):
    class Meta:
        model = User


class UserAdmin (BaseUserAdmin):
    form = UserChangeForm
    add_form = UserCreationForm

    list_display = BaseUserAdmin.list_display + ('date_joined', 'last_login', 'visible_on_home', 'found_on_twitter')
    list_editable = BaseUserAdmin.list_editable + ('visible_on_home',)

    def found_on_twitter(self, obj):
        return not obj.sm_not_found
    found_on_twitter.boolean = True


class AppConfigAdmin (admin.ModelAdmin):
    class Meta:
        model = AppConfig

    fieldsets = (
        (None, {'fields': ('title', 'subtitle', 'description')}),
        ('Interface Text', {'fields': (
            'app_label', 'app_description',
            'vision', 'vision_plural',
            'visionary', 'visionary_plural',
            'visionaries_label', 'visionaries_description',
            'ally', 'ally_plural',
            'allies_label', 'allies_description',
            'add_vision_text', 'city')}),
        ('Twitter Integration Configuration', {'fields': (
            'twitter_handle',
            'twitter_consumer_key', 'twitter_consumer_secret',
            'twitter_access_token', 'twitter_access_token_secret',
            'twitter_tracking_keywords',)}),
        ('Sharing', {'fields': ('share_title', 'url')}),
        ('App Walkthrough Text', {'fields': ('show_walkthrough',
            'walkthrough_title_1', 'walkthrough_description_1',
            'walkthrough_title_2', 'walkthrough_description_2',
            'walkthrough_title_3', 'walkthrough_description_3',
            )}),
    )


admin.site.register(AppConfig, AppConfigAdmin)
admin.site.register(Vision, VisionAdmin)
admin.site.register(User, UserAdmin)
admin.site.register(Reply, ReplyAdmin)
admin.site.register(Category, CategoryAdmin)
admin.site.register(Tweet, TweetAdmin)

/*globals Backbone $ */

var VisionLouisville = VisionLouisville || {};

(function(NS) {
  'use strict';

  Backbone.Relational.store.addModelScope(NS);

  // Visions ==================================================================
  NS.VisionModel = Backbone.RelationalModel.extend({
    relations: [{
      type: Backbone.HasMany,
      key: 'replies',
      relatedModel: 'ReplyModel',
      collectionType: 'ReplyCollection',
      reverseRelation: {
        key: 'vision',
        includeInJSON: Backbone.Model.prototype.idAttribute,
      }
    },{
      type: Backbone.HasMany,
      key: 'supporters',
      relatedModel: 'UserModel',
    },{
      type: Backbone.HasMany,
      key: 'sharers',
      relatedModel: 'UserModel',
    }]
  });

  NS.VisionCollection = Backbone.Collection.extend({
    url: '/api/visions/',
    comparator: 'created_at',
    model: NS.VisionModel
  });

  NS.InputStreamCollection = Backbone.Collection.extend({
    url: '/api/stream/'
  });

  // Replies ==================================================================
  NS.ReplyModel = Backbone.RelationalModel.extend({});

  NS.ReplyCollection = Backbone.Collection.extend({
    url: '/api/replies/',
    comparator: 'created_at',
    model: NS.ReplyModel
  });

  // Users ====================================================================
  NS.UserModel = Backbone.RelationalModel.extend({
    support: function(vision) {
      var supporters = vision.get('supporters');

      if (!supporters.contains(this)) {
        supporters.add(this);

        $.ajax({
          type: 'PUT',
          url: vision.url() + '/support',
          error: function() { supporters.remove(this); }
        });
      }
    },
    unsupport: function(vision) {
      var supporters = vision.get('supporters');

      if (supporters.contains(this)) {
        supporters.remove(this);

        $.ajax({
          type: 'DELETE',
          url: vision.url() + '/support',
          error: function() { supporters.add(this); }
        });
      }
    },
    share: function(vision) {
      var sharers = vision.get('sharers');

      if (!sharers.contains(this)) {
        sharers.add(this);

        $.ajax({
          type: 'POST',
          url: vision.url() + '/share',
          error: function() { sharers.remove(this); }
        });
      }
    },
    isAuthenticated: function() {
      return !this.isNew();
    }
  });

  NS.UserCollection = Backbone.Collection.extend({
    url: '/api/users/',
    model: NS.UserModel
  });

}(VisionLouisville));
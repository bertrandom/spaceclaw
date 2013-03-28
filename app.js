var net = require('net');
var util = require('util');
var events = require('events');
var exec = require('child_process').exec;

var _ = require('underscore');
var SolTelnet = require('sol-telnet');
var express = require('express');
var OAuth = require('oauth').OAuth;
var redis = require("redis");
var RedisStore = require('connect-redis')(express);
var colorize = require('colorize');

var secrets = require('./config/secrets.json');
var logo_ansis = require('./data/logo.json');
var words = require('./data/words.json');

var rc = redis.createClient();
var app = express();

var Y = require('yui').use('base');

var config = {
  host: 'spaceclaw.net'
};

if (typeof process.env.SPACE_CLAW_DEV !== 'undefined') {
  config.host = 'spaceclaw.com';
  config.port = '8080';
  config.admin = '61091860@N00';
  config.backdoor = secrets.backdoor_passphrase;
}

app.use(express.static(__dirname + '/public'));
app.use(express.cookieParser());
app.use(express.session({ secret: secrets.express_session_secret, store: new RedisStore }));
app.use(express.bodyParser());

app.set('views', __dirname + '/public');
app.engine('html', require('ejs').renderFile);

var oa = new OAuth("http://www.flickr.com/services/oauth/request_token",
  "http://www.flickr.com/services/oauth/access_token",
  secrets.flickr_api_key,
  secrets.flickr_api_secret,
  "1.0A",
  "http://" + config.host + (typeof config.port !== 'undefined' ? ':' + config.port : '') + "/callback",
  "HMAC-SHA1");

app.get('/', function(req, res){
  res.render('index.html');
});

app.get('/loggedin', function(req, res){
  res.render('loggedin.html');
});

app.get('/login/:sessionId/', function(req, res){

  oa.getOAuthRequestToken(function(error, oauthToken, oauthTokenSecret, results) {

    if (error) {
      res.send('Flickr has the hiccups, please try again later.', 500);
    } else {
        
      req.session.oauthRequestToken = oauthToken;
      req.session.oauthRequestTokenSecret = oauthTokenSecret;
      req.session.sessionId = req.params.sessionId;
      res.redirect("http://www.flickr.com/services/oauth/authorize?oauth_token=" + oauthToken + '&perms=read');
        
    }

  });

});

// Replaces the silly _content objects with the actual content in Flickr JSON
function fixFlickrJSON(data) {

    var content = null;
    
    for (var key in data) {

        if (key == '_content') {
           content = data._content;
           break;
        } else if (typeof(data[key]) == 'object') {
            data[key] = fixFlickrJSON(data[key]);
        }
        
    }
    
    if (content != null) {
        data = content;
    }
    
    return data;
    
}

app.get('/callback', function(req, res) {
    
  oa.getOAuthAccessToken(req.query.oauth_token, req.session.oauthRequestTokenSecret, req.query.oauth_verifier, function(error, oauth_access_token, oauth_access_token_secret, results) {
      
    if (error) {
      res.send('There was a problem connecting Space Claw to Flickr, please try again later.', 500);
    } else {
                    
      oa.getProtectedResource("http://api.flickr.com/services/rest?nojsoncallback=1&format=json&method=flickr.people.getInfo&user_id=" + results.user_nsid, "GET", oauth_access_token, oauth_access_token_secret,  function (error, data, response) {

        if (error) {
          
          res.send('Flickr has the hiccups, please try again later.', 500);

        } else {
            
          data = JSON.parse(data);
          data = fixFlickrJSON(data);
          
          var user_id = data.person.nsid;
          
          var user = data.person;
          
          user.oauth_access_token = oauth_access_token;
          user.oauth_access_token_secret = oauth_access_token_secret;

          delete user.timezone;
          delete user.photos;

          // redis hmset doesn't like integers, this makes everything strings
          _.each(user, function(value, key, list) {
            list[key] = '' + value;
          });

          rc.hmset('user:' + user_id, user);

          req.session.user_id = user_id;

          Y.fire('handleAuth', {
            sessionId: req.session.sessionId,
            user: user
          });

          res.redirect('/loggedin');

        }

      });
        
    }
      
  });
     
});

var Photostream = Y.Base.create('Photostream', Y.Base, [], {

  session: null,
  photos: [],

  initializer: function(cfg) {
    this.session = cfg.session;
    this.session.telnetStream.on('lineReceived', Y.bind(this.lineReceived, this));
    this.displayHeader();

    var url = "http://api.flickr.com/services/rest?nojsoncallback=1&format=json&method=flickr.people.getPhotos&user_id=" + 
      this.session.get('user').nsid + '&extras=url_s,path_alias&per_page=25';

    this.session.callFlickr(url, Y.bind(function (error, data, response) {

      if (error) {
        this.sendLine('Flickr has the hiccups, please try again later.');
        this.session.telnetStream.end();
        return;
      }

      var result = JSON.parse(data);

      if (result.stat != 'ok') {
        this.sendLine('Flickr has the hiccups, please try again later.');
        this.session.telnetStream.end();
        return;
      }

      this.photos = result.photos.photo;

      this.set('page', result.photos.page);
      this.set('pages', result.photos.pages);
      this.set('cursor', 1);
      this.displayCurrentPhoto();

    }, this));

  },

  destructor : function() {
    this.session.telnetStream.removeAllListeners('lineReceived');
  },

  send: function(data) {
    this.session.send(data);
  },

  sendLine: function(line) {
    this.session.sendLine(line);
  },

  lineReceived: function(line) {

    switch (line) {

      case '':
        this.set('cursor', this.get('cursor') + 1);

        if (this.get('cursor') > this.photos.length) {
          if (this.get('page') < this.get('pages')) {

            var url = "http://api.flickr.com/services/rest?nojsoncallback=1&format=json&method=flickr.people.getPhotos&user_id=" + 
              this.session.get('user').nsid + '&extras=url_s,path_alias&per_page=25&page=' + (this.get('page') + 1);

            this.session.callFlickr(url, Y.bind(function (error, data, response) {

              if (error) {
                this.sendLine('Flickr has the hiccups, please try again later.');
                this.session.telnetStream.end();
                return;
              }

              var result = JSON.parse(data);

              if (result.stat != 'ok') {
                this.sendLine('Flickr has the hiccups, please try again later.');
                this.session.telnetStream.end();
                return;
              }

              Y.Array.each(result.photos.photo, Y.bind(function(photo) {
                this.photos.push(photo);
              }, this));

              this.set('page', result.photos.page);
              this.displayCurrentPhoto();

            }, this));

          } else {

            // Loop back around

            this.set('cursor', 1);
            this.set('page', 1);
            this.displayCurrentPhoto();

          }
        } else {
          this.displayCurrentPhoto();
        }

        break;

      case 'q':
        this.session.route('mainmenu');
        break;

      default:
        this.sendLine('');
        this.sendLine(colorize.ansify('#red[Invalid option.]'));
        this.sendLine('');
        break;

    }

  },

  displayHeader: function() {

    this.sendLine('');
    this.sendLine(colorize.ansify('#bold[Your photostream]'));
    this.sendLine('');

  },

  displayCurrentPhoto: function() {

    var photo = this.photos[this.get('cursor') - 1];

    var url = 'http://www.flickr.com/photos/' + ((typeof photo.pathalias !== 'undefined' && photo.pathalias && photo.pathalias != '') ? photo.pathalias : photo.owner) + '/' + photo.id + '/';

    var option = '';

    // Multiply by Monaco font proportions to determine whether the window is wider or taller
    if (this.session.get('width') * 7 > this.session.get('height') * 17) {
      option = '--height=' + (this.session.get('height') - 7);
    } else {
      option = '--width=' + this.session.get('width');
    }

    if (this.session.get('colors') && typeof process.env.SPACE_CLAW_DEV === 'undefined') {
      option += ' --colors';
    }

    exec('jp2a ' + option + ' ' + photo.url_s, Y.bind(function callback(error, stdout, stderr){

      var lines = stdout.split("\n");
      var longest_line = 0;
      for (var i = 0; i < lines.length; i++) {
        if (lines[i].length > longest_line) {
          longest_line = lines[i].replace(/\033\[[0-9;]*m/g, "").length;
        }
      }

      for (var i = url.length; i < longest_line; i++) {
        url = ' ' + url;
      }

      this.sendLine('');
      this.send(stdout);
      this.sendLine('');
      if (photo.title.trim() != '') {
        this.sendLine(photo.title);
      } else {
        this.sendLine('Untitled');
      }

      this.sendLine(url);
      this.sendLine('');

      this.sendLine('[' + colorize.ansify('#bold[enter]') + '] to go to the next photo, [' + colorize.ansify('#bold[q]') + '] to return to main menu');

    }, this));

  },

  NAME: 'photostream',
  ATTRS: {
    cursor: {
      value: null
    },
    page: {
      value: null
    },
    pages: {
      value: null
    }
  }

}, '1.0.0');

var ContactsPhotos = Y.Base.create('ContactsPhotos', Y.Base, [], {

  session: null,
  photos: [],

  initializer: function(cfg) {
    this.session = cfg.session;
    this.session.telnetStream.on('lineReceived', Y.bind(this.lineReceived, this));
    this.displayHeader();

    var url = "http://api.flickr.com/services/rest?nojsoncallback=1&format=json&method=flickr.photos.getContactsPhotos&extras=url_s,path_alias&count=50";

    this.session.callFlickr(url, Y.bind(function (error, data, response) {

      if (error) {
        this.sendLine('Flickr has the hiccups, please try again later.');
        this.session.telnetStream.end();
        return;
      }

      var result = JSON.parse(data);

      if (result.stat != 'ok') {
        this.sendLine('Flickr has the hiccups, please try again later.');
        this.session.telnetStream.end();
        return;
      }

      this.photos = result.photos.photo;

      this.set('cursor', 1);
      this.displayCurrentPhoto();

    }, this));

  },

  destructor : function() {
    this.session.telnetStream.removeAllListeners('lineReceived');
  },

  send: function(data) {
    this.session.send(data);
  },

  sendLine: function(line) {
    this.session.sendLine(line);
  },

  lineReceived: function(line) {

    switch (line) {

      case '':
        this.set('cursor', this.get('cursor') + 1);

        if (this.get('cursor') > this.photos.length) {

          // Loop back around

          this.set('cursor', 1);
          this.displayCurrentPhoto();

        } else {
          this.displayCurrentPhoto();
        }

        break;

      case 'q':
        this.session.route('mainmenu');
        break;

      default:
        this.sendLine('');
        this.sendLine(colorize.ansify('#red[Invalid option.]'));
        this.sendLine('');
        break;

    }

  },

  displayHeader: function() {

    this.sendLine('');
    this.sendLine(colorize.ansify('#bold[Photos from your contacts]'));
    this.sendLine('');

  },

  displayCurrentPhoto: function() {

    var photo = this.photos[this.get('cursor') - 1];

    var url = 'http://www.flickr.com/photos/' + ((typeof photo.pathalias !== 'undefined' && photo.pathalias && photo.pathalias != '') ? photo.pathalias : photo.owner) + '/' + photo.id + '/';

    var option = '';

    // Multiply by Monaco font proportions to determine whether the window is wider or taller
    if (this.session.get('width') * 7 > this.session.get('height') * 17) {
      option = '--height=' + (this.session.get('height') - 7);
    } else {
      option = '--width=' + this.session.get('width');
    }

    if (this.session.get('colors') && typeof process.env.SPACE_CLAW_DEV === 'undefined') {
      option += ' --colors';
    }

    exec('jp2a ' + option + ' ' + photo.url_s, Y.bind(function callback(error, stdout, stderr){

      var lines = stdout.split("\n");
      var longest_line = 0;
      for (var i = 0; i < lines.length; i++) {
        if (lines[i].length > longest_line) {
          longest_line = lines[i].replace(/\033\[[0-9;]*m/g, "").length;
        }
      }

      for (var i = url.length; i < longest_line; i++) {
        url = ' ' + url;
      }

      this.sendLine('');
      this.send(stdout);
      this.sendLine('');
      if (photo.title.trim() != '') {
        this.sendLine(photo.title + ' by ' + colorize.ansify('#bold[' + photo.username + ']'));
      } else {
        this.sendLine('Untitled' + ' by ' + photo.username);
      }

      this.sendLine(url);
      this.sendLine('');

      this.sendLine('[' + colorize.ansify('#bold[enter]') + '] to go to the next photo, [' + colorize.ansify('#bold[q]') + '] to return to main menu');

    }, this));

  },

  NAME: 'contactsphotos',
  ATTRS: {
    cursor: {
      value: null
    }
  }

}, '1.0.0');

var MainMenu = Y.Base.create('MainMenu', Y.Base, [], {

  session: null,
  lineReceivedSubscription: null,

  initializer: function(cfg) {
    this.session = cfg.session;
    this.session.telnetStream.on('lineReceived', Y.bind(this.lineReceived, this));
    this.displayOptions();
  },

  destructor : function() {
    this.session.telnetStream.removeAllListeners('lineReceived');
  },

  send: function(data) {
    this.session.send(data);
  },

  sendLine: function(line) {
    this.session.sendLine(line);
  },  

  displayOptions: function() {

    this.sendLine('');
    this.sendLine('[' + colorize.ansify('#bold[1]') + '] Your Photostream');
    this.sendLine('[' + colorize.ansify('#bold[2]') + '] Photos from your contacts');
    this.sendLine('[' + colorize.ansify('#bold[3]') + '] Color: ' + (this.session.get('colors') ? colorize.ansify('#green[ON]') : 'OFF'));
    this.sendLine('[' + colorize.ansify('#bold[4]') + '] Sign out');
    this.sendLine('');

  },

  lineReceived: function(line) {

    switch (line) {

      case '1':
        this.session.route('photostream');
        break;

      case '2':
        this.session.route('contactsphotos');
        break;

      case '3':

        this.session.set('colors', !this.session.get('colors'));

        this.sendLine('');

        if (this.session.get('colors')) {
          this.sendLine(colorize.ansify('Color is now #green[ON].'));
        } else {
          this.sendLine(colorize.ansify('Color is now OFF.'));
        }

        this.displayOptions();
        break;

      case '4':
        this.sendLine('');
        this.sendLine('NO CARRIER');
        this.sendLine('');
        this.session.telnetStream.end();
        break;

      default:
        this.sendLine('');
        this.sendLine(colorize.ansify('#red[Invalid option.]'));
        this.displayOptions();
        break;

    }

  },

  NAME: 'mainMenu',
  ATTRS: {

  }

}, '1.0.0');

var TelnetSession = Y.Base.create('TelnetSession', Y.Base, [], {

  telnetStream: null,

  initializer: function(cfg) {

    this.set('remaininglogins', 3);
    this.set('colors', false);

    this.set('sessionId', this.generateSessionId());

    this.telnetStream = cfg.telnetStream;
    this.telnetStream.on('windowSizeChange', Y.bind(this.windowSizeChange, this));
    
    this.on("resizedChange", Y.bind(function() {
      this.displayLogo();
      this.displayLoginPrompt();
      Y.on("handleAuth", Y.bind(this.handleAuth, this));
      this.telnetStream.on('lineReceived', Y.bind(this.lineReceived, this));
    }, this));

  },

  lineReceived: function(line) {

    if (line != '') {

      line = line.replace(/ /g, '-');

      // In dev mode, login as admin using a backdoor:
      if (typeof process.env.SPACE_CLAW_DEV !== 'undefined' && line == config.backdoor) {

        rc.hgetall('user:' + config.admin, Y.bind(function (err, user) {

          this.set('user', user);
          this.sendLine('');
          this.welcomeUser(true);
          this.route('mainmenu');

        }, this));
        return;

      }

      rc.get("passphrase:" + line, Y.bind(function(err, reply) {

        if (reply == null) {

          this.set('remaininglogins', this.get('remaininglogins') - 1);

          if (this.get('remaininglogins') == 0) {

            this.sendLine('');
            this.sendLine(colorize.ansify("#bold[#red[Invalid passphrase.]] If you're happy and you know it, Connection closed by foreign host."));
            this.sendLine('');
            this.telnetStream.end();

          } else {

            this.sendLine('');
            this.sendLine(colorize.ansify("#bold[#red[Invalid passphrase, please try again.]] (" + this.get('remaininglogins') + " login attempts remaining)"));
            this.sendLine('');

          }         

        } else {

          var user_id = reply;

          rc.hgetall('user:' + user_id, Y.bind(function (err, user) {

            this.set('user', user);
            this.sendLine('');
            this.welcomeUser(true);
            this.route('mainmenu');

          }, this));

        }

      }, this));
    }

  },  

  send: function(data) {
    this.telnetStream.send(data);
  },

  sendLine: function(line) {
    this.telnetStream.sendLine(line);
  },

  callFlickr: function(url, callback) {
    oa.getProtectedResource(url, "GET", this.get('user').oauth_access_token, this.get('user').oauth_access_token_secret, callback);
  },

  handleAuth: function(authData) {

    if (authData.sessionId == this.get('sessionId')) {
      
      this.generateUniquePassphrase(Y.bind(function(passphrase) {

        rc.hget("user:" + authData.user.nsid, "passphrase", Y.bind(function(err, oldpassphrase) {

          if (oldpassphrase) {
            rc.del("passphrase:" + oldpassphrase);
          }

          authData.user.passphrase = passphrase;

          rc.hset("user:" + authData.user.nsid, "passphrase", passphrase);
          rc.set("passphrase:" + passphrase, authData.user.nsid);

          this.set('user', authData.user);
          this.welcomeUser();
          this.route('mainmenu');

        }, this));

      }, this));


    }

  },
  
  generateSessionId: function() {

    var sessionId = '' + _.random(1,9);

    for (var i = 0; i < 5; i++) {
      sessionId += _.random(0,9);
    }

    return sessionId;

  },

  generatePassphrase: function() {

    var passphrase = [];

    var words_length = words.length;
    for (var i = 0; i < 4; i++) {
      var index = _.random(0,words_length - 1);
      passphrase.push(words[index]);
    }

    return passphrase.join('-');

  },

  generateUniquePassphrase: function(callback) {

    var passphrase = this.generatePassphrase();
    rc.get('passphrase:' + passphrase, Y.bind(function(err, reply) {
      if (reply == null) {
        callback(passphrase);
      } else {
        this.generateUniquePassphrase(callback);
      }
    }, this));

  },

  windowSizeChange: function(width, height) {
    this.set('width', width);
    this.set('height', height);

    if (typeof this.get('resized') === 'undefined') {
      this.set('resized', true);
    }

  },

  displayLogo: function() {

    var width = this.get('width');

    this.sendLine("");

    if (width >= 80 && width <= 200) {
      this.send(logo_ansis[width]);
    } else if (width > 200) {
      this.send(logo_ansis[200]);
    }

    this.send("\033[0m");

    this.sendLine("");

  },

  displayLoginPrompt: function() {

    this.sendLine("");
    this.sendLine(colorize.ansify("Please go to this URL to login: #bold[http://" + config.host + (typeof config.port !== 'undefined' ? ':' + config.port : '') + "/login/" + this.get('sessionId') + '/]' + " or enter your passphrase."));
    this.sendLine("");

  },

  welcomeUser: function(suppressPassphrase) {

    var user = this.get('user');
    this.sendLine(colorize.ansify("#bold[#green[Login successful.]]"));
    this.sendLine('');
    this.sendLine('Hi ' + user.username + '!');

    if (typeof suppressPassphrase === 'undefined') {

      this.sendLine('');
      this.sendLine('If you\'d like to easily login in the future, you can use this passphrase:');
      this.sendLine(user.passphrase.replace(/-/g, ' '));

    }

  },

  route: function(uri) {

    if (this.get('uri') == uri) {
      return;
    } else {
      var page = this.get('page');
      if (page) {
        page.destroy();
      } else {
        this.telnetStream.removeAllListeners('lineReceived');
      }
    }

    this.set('uri', uri);

    switch (uri) {

      case 'mainmenu':

        var mainMenu = new MainMenu({
          session: this
        });

        this.set('page', mainMenu);
        break;

      case 'photostream':
        var photostream = new Photostream({
          session: this
        });

        this.set('page', photostream);
        break;

      case 'contactsphotos':
        var contactsphotos = new ContactsPhotos({
          session: this
        });

        this.set('page', contactsphotos);
        break;

    }

  },

  NAME: 'telnetSession',
  ATTRS: {
    uri: {
      value: 'login'
    },
    sessionId: {
      value: null
    },
    width: {
      value: null
    },
    height: {
      value: null
    },
    remaininglogins: {
      value: null
    },
    resized: {
      writeOnce: true
    },
    colors: {
      value: null
    },
    user: {},
    page: {},
  }

}, '1.0.0');

server = net.createServer(function(sock) {
  SolTelnet.TelnetStream(sock, function(telnetStream) {
    var telnetSession = new TelnetSession({
      telnetStream: telnetStream
    });
  })
});

server.listen(23);
app.listen(8080);
# space claw

Almost certainly the best Flickr BBS.

If you'd like to see it in action, open a terminal and type:

	telnet spaceclaw.net

![screenshot](http://farm9.staticflickr.com/8249/8594938142_393d33de80_z.jpg)

## Installation

### Development Server setup

Install node and npm through macports or homebrew.

	sudo port install nodejs
	
Install redis

	sudo port install redis
	
Install jp2a
	
	sudo port install jp2a
	
Unfortunately, the version of jp2a on macports does not support ANSI --colors, so you can't see them on the dev server, but Ubuntu 12.10's jp2a does support colors.
	
Add an entry in your hosts file for spaceclaw.com:

	127.0.0.1 spaceclaw.com
	
Copy `config/secrets.json.example.json` to `config/secrets.json` and set your express session secret and your Flickr API key and secret.

Install the dependencies:

	npm install
	
Start the server (the telnet server and web app both run in the same daemon)

	sudo SPACE_CLAW_DEV=1 node app
	
In another window:

	telnet localhost
	
After you've logged-in successfully on dev, you can modify the `config.admin` variable to point to your NSID, and then you can use the backdoor passphrase to easily login and test stuff. The backdoor is only available in the dev environment.

### Production Server setup

Pretty much the same, except apt-get everything. I'm running it through forever in case it crashes.

There are other webservers running on the same server, but I have a few IP addresses, so I map traffic from port 80 on one of them to 8080 by setting this at the top of `/etc/ufw/before.rules`

	*nat
	:PREROUTING ACCEPT [0:0]
	-A PREROUTING -p tcp -d 65.60.42.115 --dport 80 -j DNAT --to-destination 127.0.0.1:8080
	COMMIT
	

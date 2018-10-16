#!/usr/bin/python
# -*- coding: utf-8 -*-

import pygame
import time
import random
import websocket
import json
import logging
import urllib3
import yaml
import sys

from Queue import Queue
from threading import Thread

from xivo_auth_client import Client as Auth
from xivo_ctid_ng_client import Client as CtidNg
from xivo_confd_client import Client as Confd

urllib3.disable_warnings()
logging.basicConfig()
logging.captureWarnings(True)


####################

pygame.init()
pygame.mixer.init()
pygame.font.init()

screenW = 700
screenH = 500
screen_opt = 0

if len(sys.argv) == 2 and sys.argv[1] == '-f':
    screen_opt = pygame.FULLSCREEN

screen = pygame.display.set_mode((screenW, screenH), screen_opt)
pygame.display.set_caption('Varlese')
font = pygame.font.Font(pygame.font.get_default_font(), 10)

laser = pygame.mixer.Sound('data/laser.wav')
ex = pygame.mixer.Sound('data/ex.wav')
music = pygame.mixer.Sound('data/music.wav')

clandestine = False
done = False
pos = (0, 0)
toggle = 0
bn = 0
score = 0
keyboard_speed = 0.5
started = False
kill_by = None
kill_by_call_id = None
chan_list = []
sprite_list = []

enemy_images = ['data/sa.png', 'data/sa2.png', 'data/sa3.png']

q = Queue()
hangup_queue = Queue()

RUNNING, PAUSE = 0, 1
state = RUNNING


#####################

class Wazo:
    def __init__(self, config):
        self.host = config['wazo']['host']
        self.username = config['wazo']['username']
        self.password = config['wazo']['password']
        self.port = config['wazo']['port']
        self.backend = config['wazo']['backend']
        self.application_uuid = config['wazo']['application_uuid']
        self.mobile = str(config['mobile'])
        self.context = config['context']
        self.did = config['did']
        self.expiration = 3600
        self.token = None
        self.user_uuid = None
        self.call_control = None
        self.confd = None

    def connect(self):
        self._get_token()
        self.callcontrol = CtidNg(self.host, token=self.token, prefix='api/ctid-ng', port=self.port, verify_certificate=False)
        self.confd = Confd(self.host, token=self.token, prefix='api/confd', port=self.port, verify_certificate=False)
        self._websocket()
        self._hangup_worker()

    def _websocket(self):
        t = Thread(target=worker, args=(self,))
        t.daemon = True
        t.start()

    def _hangup_worker(self):
        for n in range(10):
            t = Thread(target=hangup_worker, args=(self,))
            t.daemon = True
            t.start()

    def hangup(self, call_id):
        playback = {
            'uri': 'sound:tt-weasels',
        }
        self.callcontrol.applications.send_playback(self.application_uuid, call_id, playback)
        time.sleep(2)
        self.callcontrol.applications.hangup_call(self.application_uuid, call_id)

    def list_calls(self):
        calls = {'items': []}

        if not self.token:
            print 'error: token'
            return calls

        call = self.callcontrol.applications.list_calls(self.application_uuid)
        for c in call['items']:
            calls['items'].append(c)

        return calls

    def make_call(self, call_id):
        print 'Call {}'.format(self.mobile)
        calls = {'calls': [{'id': call_id}]}
        node = self.callcontrol.applications.create_node(self.application_uuid, calls)
        call = {
            'autoanswer': False,
            'context': self.context,
            'exten': self.mobile
        }
        self.callcontrol.applications.make_call_to_node(self.application_uuid, node['uuid'], call)

    def _get_token(self):
        auth = Auth(self.host, username=self.username, password=self.password, prefix='api/auth', port=self.port, verify_certificate=False)
        token_data = auth.token.new(self.backend, expiration=self.expiration)
        self.token = token_data['token']
        self.user_uuid = token_data['xivo_user_uuid']


class Sprite:
    def __init__(self, image_path='data/sa.png'):
        self.direction = 1
        self.slowness = 1
        self.x = 0
        self.y = 0
        self.image = pygame.image.load(image_path)
        self.image = pygame.transform.scale(self.image, (30, 30))
        self.width = 30
        self.height = 30

    def update(self):
        if random.randint(1, 750) == 1:
            enemybullet = Enemybullet()
            enemybullet.wazo = self.wazo
            sprite_list.append(enemybullet)
            enemybullet.x = self.x + 15
            enemybullet.y = self.y + 30
        if toggle % self.slowness == 0:
            if self.x < 0:
                self.direction = 1
            elif self.x > screenW - self.width:
                self.direction = -1
            if self.y < screenH + 150:  # change to 'screenH-150' if want bad guys to stop going L/R before end
                self.x += self.direction
            self.y += .1
        if self.y > screenH + self.width:
            sprite_list.remove(self)
            if self.wazo.get('alien'):
                hangup_queue.put(self.wazo.get('call_id'), False)


class Alien(Sprite):
    def __init__(self, x, y, slowness, wazo=None):
        Sprite.__init__(self, enemy_images[random.randrange(0, len(enemy_images))])
        self.x = x
        self.y = y
        self.slowness = slowness
        sprite_list.append(self)
        self.alien = True
        self.wazo = wazo


class Rectangle:
    def __init__( self, x, y, width, height):
        self.left = x
        self.top = y
        self.bottom = y + height
        self.right = x + width


class Player(Sprite):
    def __init__(self):
        Sprite.__init__(self, 'data/p.png')
        self.image = pygame.transform.scale(self.image, (30, 30))
        self.x = screenW / 2
        self.y = screenH - 80
        self.width = 30
        self.height = 30
        self.speedx = 0
        self.speedy = 0

    def update(self):
        self.x = self.x + self.speedx
        self.y = self.y + self.speedy
        if self.x < 0:
            self.x -= self.speedx
        if self.x > screenW - 30:
            self.x -= self.speedx
        for sprite in sprite_list:
            if sprite != self and not hasattr(sprite, 'bullet'):
                self_rectangle = Rectangle(self.x, self.y, self.width, self.height)
                other_rectangle = Rectangle(sprite.x, sprite.y, sprite.width, sprite.height)
                if rectangular_intersection(self_rectangle, other_rectangle) and clandestine == False:
                    global kill_by, kill_by_call_id
                    if sprite.wazo:
                        kill_by = sprite.wazo.get('caller_id')
                        kill_by_call_id = sprite.wazo.get('call_id')


class Bullet(Sprite):
    def __init__(self):
        Sprite.__init__(self, 'data/b.png')
        self.image = pygame.transform.scale(self.image, (8, 12))
        self.width = 8
        self.height = 12
        self.bullet = True
        laser.play()

    def update(self):
        global bn, score

        kill_list = []
        self_rectangle = Rectangle(self.x, self.y, self.width, self.height)

        for sprite in sprite_list:

            if hasattr(sprite, 'alien'):
                other_rectangle = Rectangle(sprite.x, sprite.y, sprite.width, sprite.height)
                if rectangular_intersection(self_rectangle, other_rectangle):
                    kill_list.append(sprite)
                    ex.play()
                    if sprite.wazo:
                        hangup_queue.put(sprite.wazo.get('call_id'), False)
                    if self not in kill_list:
                        kill_list.append(self)
                        bn -= 1

            if hasattr(sprite, 'enemybullet'):
                other_rectangle = Rectangle(sprite.x, sprite.y, sprite.width, sprite.height)
                if rectangular_intersection(self_rectangle, other_rectangle):
                    kill_list.append(sprite)
                    if self not in kill_list:
                        kill_list.append(self)
                        bn -= 1

        if self.y < 0:
            kill_list.append(self)
            bn -= 1

        for sprite in kill_list:
            if sprite in sprite_list:
                sprite_list.remove(sprite)
                if hasattr(sprite, 'alien'):
                    score += 100
        self.y -= 1


class Enemybullet(Sprite):
    def __init__(self):
        Sprite.__init__(self, 'data/eb.png')
        self.image = pygame.transform.scale(self.image, (8, 12))
        self.width = 8
        self.height = 12
        self.enemybullet = True
        self.wazo = None

    def update(self):
        kill_list = []
        if self.y > screenH:
            kill_list.append(self)
        for sprite in kill_list:
            if sprite in sprite_list:
                sprite_list.remove(sprite)
        self.y += 1


def rectangular_intersection(rect1, rect2):
    return not (rect1.right < rect2.left or rect1.left > rect2.right
                or rect1.bottom < rect2.top or rect1.top > rect2.bottom)

def draw_frame(alist, toggle, number):
    global score, name

    pygame.draw.rect(screen, (0, 0, 0), screen.get_rect())
    screen.blit(star, (0, 0))
    scorenumber = font.render(str(score), True, (255, 255, 255))
    screen.blit(scorenumber, (10, screenH - 60))
    scorem = font.render('SCORE', True, (255, 255, 255))
    screen.blit(scorem, (10, screenH - 80))
    namem = font.render(str(name), True, (255, 255, 255))
    screen.blit(namem, (10, screenH - 100))
    largeText = pygame.font.Font('freesansbold.ttf', 80)
    r  = largeText.render(number, True, (255, 255, 255))
    screen.blit(r, (0, 0))

    for sprite in alist:
        position = (sprite.x, sprite.y)
        screen.blit(sprite.image, position)
    pygame.display.flip()

def update_sprites():
    global toggle, randomcreation, lastupdate
    toggle = toggle + 1

    for sprite in sprite_list:
        sprite.update()

def show_kill_by(caller):
    largeText = pygame.font.Font('freesansbold.ttf', 80)
    r  = largeText.render('KILL BY', True, (255, 255, 255))
    screen.blit(r, (50, screenH / 2 - 150 ))
    r  = largeText.render(caller, True, (255, 255, 255))
    screen.blit(r, (0, screenH / 2))
    pygame.display.update()


def hangup_worker(wazo):
    while True:
        r = hangup_queue.get()
        wazo.hangup(r)

def worker(wazo):

    events = [
        'application_call_entered',
        'application_call_initiated',
	'application_call_updated',
        'application_call_deleted',
    ]

    def subscribe(ws, event_name):
        ws.send(json.dumps({
            'op': 'subscribe',
            'data': {
                'event_name': event_name
            }
        }))

    def start(ws):
        msg = {'op': 'start'}
        ws.send(json.dumps(msg))

    def subscribe_events(msg):
        data = msg['data']
        name = msg['name']

        if name == 'application_call_entered':
            for alien in _generate_alien_from_wazo([data['call']]):
                q.put(alien)
        if name == 'application_call_deleted':
            alien_call_id = data.get('call').get('id')
            for sprite in sprite_list:
                if hasattr(sprite, 'wazo'):
                    if sprite.wazo.get('call_id') == alien_call_id:
                        sprite_list.remove(sprite)
                        chan_list.remove(alien_call_id)

    def init(ws, msg):
        global started

        if msg.get('op') == 'init':
            for event in events:
                subscribe(ws, event)
            start(ws)

        if msg.get('op') == 'start':
            started = True

    def on_message(ws, message):
        msg = json.loads(message)

        if started:
            subscribe_events(msg)
            return True
        else:
            init(ws, msg)

    def on_error(ws, error):
        print "### error {} ###".format(error)

    def on_close(ws):
        print "### closed ###"

    def on_open(ws):
        print "### open ###"

    websocket.enableTrace(False)
    try:
        ws = websocket.WebSocketApp("wss://{}/api/websocketd/".format(wazo.host),
                                    header=["X-Auth-Token: {}".format(wazo.token)],
                                    on_message = on_message,
                                    on_open = on_open,
                                    on_error = on_error,
                                    on_close = on_close)
        ws.run_forever(sslopt={"cert_reqs": False})
    except:
      print 'connection error to wazo'

def _generate_alien_from_wazo(data):
    global chan_list
    aliens = []

    for d in data:
        chan = d['id']
        if chan not in chan_list:
            chan_list.append(chan)
            aliens.append({
                'alien': True,
                'call_id': chan,
                'caller_id': d['caller_id_number']
            })

    return aliens

def _send_alien(wazo, alien):
    alien.update({'cls': wazo})
    Alien(random.randint(0, screenW-50), random.randint(50, 250), random.randint(5, 10), alien)

def get_config():
    with open('config.yml') as config_file:
        data = yaml.load(config_file)
    return data if data else {}

def init_alien_from_wazo(wazo):
    calls = wazo.list_calls()
    for alien in _generate_alien_from_wazo(calls['items']):
        _send_alien(wazo, alien)


#######################


config = get_config()
name = config['player_name']

starimg = pygame.image.load('data/star.jpg').convert()
star = pygame.transform.scale(starimg, (screenW, screenH))

player = Player()
sprite_list.append(player)
music.play()
channel = music.play()
randomcreation = 500
lastupdate = time.time()

wazo = Wazo(config)
wazo.connect()
init_alien_from_wazo(wazo)

while not done:
    for event in pygame.event.get():
        if event.type == pygame.KEYDOWN:

            if event.key == pygame.K_SPACE:
                if bn < 5:
                    bullet = Bullet()
                    sprite_list.append(bullet)
                    bullet.x = player.x + 11
                    bullet.y = player.y
                    bn += 1

            if event.key == pygame.K_LEFT:
                player.speedx = -keyboard_speed

            if event.key == pygame.K_RIGHT:
                player.speedx = keyboard_speed

            if event.key == pygame.K_q:
                clandestine = True

            if event.key == pygame.K_w:
                clandestine = False

            if event.key == pygame.K_ESCAPE:
                done = True
                pygame.quit()

        if event.type == pygame.KEYUP:

            if event.key == pygame.K_LEFT:
                player.speedx = 0

            if event.key == pygame.K_RIGHT:
                player.speedx = 0

        if bn < 0:
            bn = 0

        if event.type == pygame.QUIT:
            done = True

    if state == RUNNING:
        try:
            res = q.get(False)
            if not kill_by:
                if res.get('alien'):
                    _send_alien(wazo, res)
        except:
            pass

        draw_frame(sprite_list, 0, wazo.did)
        update_sprites()

        if not channel.get_busy():
            channel = music.play()

        if kill_by:
            print "YOU HAVE BEEN KILLED BY {}".format(kill_by)
            show_kill_by(kill_by)
            wazo.make_call(kill_by_call_id)
            state = PAUSE

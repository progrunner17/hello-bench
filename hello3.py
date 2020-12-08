#!/usr/bin/env python3

# The MIT License (MIT)
#
# Copyright (c) 2015 Tintri
# Copyright (c) 2020 Shotaro Gotanda <g.sho1500@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import os
import sys
import subprocess
import select
import random
import urllib
import urllib.request as urlreq
# import urllib.error  as urlerr
# import urllib.parse as urlparse
import time
import datetime
import json
import tempfile
import shutil
import argparse

NGINX_PORT = 20000
IOJS_PORT = 20001
NODE_PORT = 20002
REGISTRY_PORT = 20003
TMP_DIR = tempfile.mkdtemp()

parser = argparse.ArgumentParser(description='''Usage: {} [OPTIONS] [BENCHMARKS]'''.format(sys.argv[0]))
parser.add_argument('benchmarks', help='specify benchmark list delimitted by comma(,)')
parser.add_argument('--docker', default='docker', help='docker compatible binary')
parser.add_argument('--out', default='bench.out', help='specify the output file')
parser.add_argument('-t', '--add-time-postfix', default=False, action='store_true', help='specify the output file')
parser.add_argument('--op', default='run', help='(run|push|pull|tag|move)')
parser.add_argument('--registry', default='docker.io', help='image registry from which the images are pulled')
parser.add_argument('--registry2', help='TODO')
parser.add_argument('--list', default=False, action='store_true', help='show the image list for bench')
parser.add_argument('--list-json', default=False, action='store_true', help='show the image list for bench as json')
parser.add_argument('--clean', default='none', help='(first|each|none)')
parser.add_argument('--trace-file', default=None, help='trace file copy from')
parser.add_argument('--trace-dir', default=None, help='dest dir of trace file')
parser.add_argument('-v', '--verbose', default=False, action='store_true')


def exit(status):
    # cleanup
    shutil.rmtree(TMP_DIR)
    sys.exit(status)


def tmp_dir():
    tmp_dir.nxt += 1
    return os.path.join(TMP_DIR, str(tmp_dir.nxt))


tmp_dir.nxt = 0


def tmp_copy(src):
    dst = tmp_dir()
    shutil.copytree(src, dst)
    return dst


def system_like_exec(cmd, verbose=True):
    if verbose:
        p_stdout = None
        p_stderr = None
    else:
        p_stdout = subprocess.DEVNULL
        p_stderr = subprocess.PIPE
    p = subprocess.run(cmd, shell=True, stderr=p_stderr, stdout=p_stdout)
    if (p.returncode != 0):
        print(p.stderr)
    return p.returncode


class RunArgs:
    def __init__(self, env={}, arg='', stdin='', stdin_sh='sh', waitline='', mount=[]):
        self.env = env
        self.arg = arg
        self.stdin = stdin
        self.stdin_sh = stdin_sh
        self.waitline = waitline
        self.mount = mount


class Bench:
    def __init__(self, name, category='other'):
        self.name = name
        self.repo = name  # TODO: maybe we'll eventually have multiple benches per repo
        self.category = category

    def __str__(self):
        return json.dumps(self.__dict__)


class BenchRunner:
    ECHO_HELLO = set(['alpine',
                      'busybox',
                      'crux',
                      'cirros',
                      'debian',
                      'ubuntu',
                      'ubuntu-upstart',
                      'ubuntu-debootstrap',
                      'centos',
                      'fedora',
                      #                       'opensuse',
                      #                       'oraclelinux',
                      'mageia', ])

    CMD_ARG_WAIT = {'mysql': RunArgs(env={'MYSQL_ROOT_PASSWORD': 'abc'},
                                     waitline='mysqld: ready for connections'),
                    'percona': RunArgs(env={'MYSQL_ROOT_PASSWORD': 'abc'},
                                       waitline='mysqld: ready for connections'),
                    'mariadb': RunArgs(env={'MYSQL_ROOT_PASSWORD': 'abc'},
                                       waitline='mysqld: ready for connections'),
                    'postgres': RunArgs(env={'POSTGRES_PASSWORD': 'abc'},
                                        waitline='database system is ready to accept connections'),
                    'redis': RunArgs(waitline='Ready to accept connections'),
                    'crate': RunArgs(waitline='started'),
                    # following error need to be resolved
                    # [1]: max virtual memory areas vm.max_map_count [65530] is too low, increase to at least [262144] by adding `vm.max_map_count = 262144` to `/etc/sysctl.conf` or invoking `sysctl -w vm.max_map_count=262144`
                    'rethinkdb': RunArgs(waitline='Server ready'),
                    'ghost': RunArgs(waitline='Ghost boot'),
                    'glassfish': RunArgs(waitline='Running GlassFish'),
                    'drupal': RunArgs(waitline='apache2 -D FOREGROUND'),
                    #                     'elasticsearch': RunArgs(waitline='] started'),
                    #                     'cassandra': RunArgs(waitline='Listening for thrift clients'),
                    'cassandra': RunArgs(waitline='Startup complete'),
                    'httpd': RunArgs(waitline='httpd -D FOREGROUND'),
                    'jenkins': RunArgs(waitline='Jenkins is fully up and running'),
                    'jetty': RunArgs(waitline='main: Started'),
                    'mongo': RunArgs(waitline='Listening on'),
                    #                     'php-zendserver': RunArgs(waitline='Zend Server started'), # TODO:
                    'rabbitmq': RunArgs(waitline='Server startup complete'),
                    'sonarqube': RunArgs(waitline='Process[web] is up'),
                    'tomcat': RunArgs(waitline='Server startup'),
                    }

    CMD_STDIN = {'php': RunArgs(stdin='php -r "echo \\\"hello\\n\\\";"'),
                 'ruby': RunArgs(stdin='ruby -e "puts \\\"hello\\\""'),
                 'jruby': RunArgs(stdin='jruby -e "puts \\\"hello\\\""'),
                 'julia': RunArgs(stdin='julia -e \'println("hello")\''),
                 'gcc': RunArgs(stdin='cd /src; gcc main.c; ./a.out',
                                mount=[('gcc', '/src')]),
                 'golang': RunArgs(stdin='cd /go/src; go run main.go',
                                   mount=[('go', '/go/src')]),
                 #                  'clojure': RunArgs(stdin='cd /hello/hello; lein run', mount=[('clojure', '/hello')]), # TODO
                 'django': RunArgs(stdin='django-admin startproject hello'),
                 'rails': RunArgs(stdin='rails new hello'),
                 'haskell': RunArgs(stdin='"hello"', stdin_sh=None),
                 'hylang': RunArgs(stdin='(print "hello")', stdin_sh=None),
                 'java': RunArgs(stdin='cd /src; javac Main.java; java Main',
                                 mount=[('java', '/src')]),
                 'mono': RunArgs(stdin='cd /src; mcs main.cs; mono main.exe',
                                 mount=[('mono', '/src')]),
                 'r-base': RunArgs(stdin='sprintf("hello")', stdin_sh='R --no-save'),
                 #                  'thrift': RunArgs(stdin='cd /src; thrift --gen py hello.idl', mount=[('thrift', '/src')]), #TODO
                 }

    CMD_ARG = {'perl': RunArgs(arg='perl -e \'print("hello\\n")\''),
               'rakudo-star': RunArgs(arg='perl6 -e \'print("hello\\n")\''),
               'pypy': RunArgs(arg='pypy3 -c \'print("hello")\''),
               'python': RunArgs(arg='python -c \'print("hello")\''),
               'hello-world': RunArgs()}

    # values are function names
    CUSTOM = {'nginx': 'run_nginx',
              'iojs': 'run_iojs',
              'node': 'run_node',
              'registry': 'run_registry'}

    # complete listing
    ALL = dict([(b.name, b) for b in
                [Bench('alpine', 'distro'),
                 Bench('busybox', 'distro'),
                 Bench('crux', 'distro'),
                 Bench('cirros', 'distro'),
                 Bench('debian', 'distro'),
                 Bench('ubuntu', 'distro'),
                 Bench('ubuntu-upstart', 'distro'),
                 Bench('ubuntu-debootstrap', 'distro'),
                 Bench('centos', 'distro'),
                 Bench('fedora', 'distro'),
                 #                  Bench('opensuse', 'distro'),
                 #                  Bench('oraclelinux', 'distro'),
                 Bench('mageia', 'distro'),
                 Bench('mysql', 'database'),
                 Bench('percona', 'database'),
                 Bench('mariadb', 'database'),
                 Bench('postgres', 'database'),
                 Bench('redis', 'database'),
                 Bench('crate', 'database'),
                 Bench('rethinkdb', 'database'),
                 Bench('php', 'language'),
                 Bench('ruby', 'language'),
                 Bench('jruby', 'language'),
                 Bench('julia', 'language'),
                 Bench('perl', 'language'),
                 Bench('rakudo-star', 'language'),
                 Bench('pypy', 'language'),
                 Bench('python', 'language'),
                 Bench('golang', 'language'),
                 #                  Bench('clojure', 'language'),
                 Bench('haskell', 'language'),
                 Bench('hylang', 'language'),
                 Bench('java', 'language'),
                 Bench('mono', 'language'),
                 Bench('r-base', 'language'),
                 Bench('gcc', 'language'),
                 #                  Bench('thrift', 'language'),
                 Bench('cassandra', 'database'),
                 Bench('mongo', 'database'),
                 Bench('hello-world'),
                 Bench('ghost'),
                 Bench('drupal'),
                 Bench('jenkins'),
                 Bench('sonarqube'),
                 Bench('rabbitmq'),
                 Bench('registry'),
                 Bench('httpd', 'web-server'),
                 Bench('nginx', 'web-server'),
                 Bench('glassfish', 'web-server'),
                 Bench('jetty', 'web-server'),
                 #                  Bench('php-zendserver', 'web-server'),
                 Bench('tomcat', 'web-server'),
                 Bench('django', 'web-framework'),
                 Bench('rails', 'web-framework'),
                 Bench('node', 'web-framework'),
                 Bench('iojs', 'web-framework'),
                 ]])

    def __init__(self, docker='docker', registry='localhost:5000', registry2='localhost:5000'):
        self.docker = docker
        self.registry = registry
        if self.registry != '':
            self.registry += '/'
        self.registry2 = registry2
        if self.registry2 != '':
            self.registry2 += '/'

    def run_echo_hello(self, repo, verbose=True):
        cmd = '%s run %s%s echo hello' % (self.docker, self.registry, repo)
#         rc = os.system(cmd)
        rc = system_like_exec(cmd, verbose=verbose)
        assert(rc == 0)

    def run_cmd_arg(self, repo, runargs, verbose=True):
        assert(len(runargs.mount) == 0)
        cmd = '%s run ' % self.docker
        cmd += '%s%s ' % (self.registry, repo)
        cmd += runargs.arg
        if verbose:
            print(cmd)
#         rc = os.system(cmd)
        rc = system_like_exec(cmd, verbose=verbose)
        assert(rc == 0)

    def run_cmd_arg_wait(self, repo, runargs, verbose=True):
        name = '%s_bench_%d' % (repo, random.randint(1, 1000000))
        env = ' '.join(['-e %s=%s' % (k, v)
                        for k, v in list(runargs.env.items())])
        cmd = ('%s run --name=%s %s %s%s %s' %
               (self.docker, name, env, self.registry, repo, runargs.arg))
        stderr = None
        if verbose:
            print(cmd)

        p = subprocess.Popen(cmd, shell=True, bufsize=1,
                             stderr=subprocess.STDOUT,
                             stdout=subprocess.PIPE)
        while True:
            l = p.stdout.readline()
            if l == '':
                continue
            if verbose:
                print(('out: ' + l.decode().strip()))
            # are we done?
            if l.find(runargs.waitline.encode()) >= 0:
                # cleanup
                if verbose:
                    print('DONE')
                cmd = '%s kill %s' % (self.docker, name)
#                 rc = os.system(cmd)
                rc = system_like_exec(cmd, verbose=verbose)
                assert(rc == 0)
                break
        p.wait()

    def run_cmd_stdin(self, repo, runargs, verbose=True):
        cmd = '%s run ' % self.docker
        for a, b in runargs.mount:
            a = os.path.join(os.path.dirname(os.path.abspath(__file__)), a)
            a = tmp_copy(a)
            cmd += '-v %s:%s ' % (a, b)
        cmd += '-i %s%s ' % (self.registry, repo)
        if runargs.stdin_sh:
            cmd += runargs.stdin_sh  # e.g., sh -c
        if verbose:
            stderr = subprocess.STDOUT
            print(cmd)
        else:
            stderr = subprocess.DEVNULL
        p = subprocess.Popen(
            cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr)
        if verbose:
            print((runargs.stdin))
        out, _ = p.communicate(runargs.stdin.encode())
        if verbose:
            print(out)
        p.wait()
        assert(p.returncode == 0)

    def run_nginx(self, verbose=True):
        name = 'nginx_bench_%d' % (random.randint(1, 1000000))
        cmd = '%s run --name=%s -p %d:%d %snginx' % (
            self.docker, name, NGINX_PORT, 80, self.registry)
        if verbose:
            print(cmd)
            p_stdout = None
        else:
            p_stdout = subprocess.DEVNULL
        p = subprocess.Popen(cmd, shell=True, stderr=subprocess.STDOUT,
                             stdout=p_stdout)
        while True:
            try:
                #                 req = urlreq.urlopen('http://localhost:%d' % NGINX_PORT)
                req = urlreq.urlopen('http://localhost:%d' % NGINX_PORT)
                req.close()
                break
            except:
                time.sleep(0.01)  # wait 10ms
                pass  # retry
        cmd = '%s kill %s' % (self.docker, name)
#         rc = os.system(cmd)
        rc = system_like_exec(cmd, verbose=verbose)
        assert(rc == 0)
        p.wait()

    def run_iojs(self, verbose=True):
        name = 'iojs_bench_%d' % (random.randint(1, 1000000))
        cmd = '%s run --name=%s -p %d:%d ' % (self.docker, name, IOJS_PORT, 80)
        a = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'iojs')
        a = tmp_copy(a)
        b = '/src'
        cmd += '-v %s:%s ' % (a, b)
        cmd += '%siojs iojs /src/index.js' % self.registry
        if verbose:
            print(cmd)
            p_stdout = None
        else:
            p_stdout = subprocess.DEVNULL
        p = subprocess.Popen(cmd, shell=True, stderr=subprocess.STDOUT,
                             stdout=p_stdout)
        while True:
            try:
                req = urlreq.urlopen('http://localhost:%d' % IOJS_PORT)
                if verbose:
                    print((req.read().strip()))
                req.close()
                break
            except:
                time.sleep(0.01)  # wait 10ms
                pass  # retry
        cmd = '%s kill %s' % (self.docker, name)
#         rc = os.system(cmd)
        rc = system_like_exec(cmd, verbose=verbose)
        assert(rc == 0)
        p.wait()

    def run_node(self, verbose=True):
        name = 'node_bench_%d' % (random.randint(1, 1000000))
        cmd = '%s run --name=%s -p %d:%d ' % (self.docker, name, NODE_PORT, 80)
        a = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'node')
        a = tmp_copy(a)
        b = '/src'
        cmd += '-v %s:%s ' % (a, b)
        cmd += '%snode node /src/index.js' % self.registry
        if verbose:
            print(cmd)
            p_stdout = None
        else:
            p_stdout = subprocess.DEVNULL
        p = subprocess.Popen(cmd, shell=True, stderr=subprocess.STDOUT,
                             stdout=p_stdout)
        while True:
            try:
                req = urlreq.urlopen('http://localhost:%d' % NODE_PORT)
                if verbose:
                    print((req.read().strip()))
                req.close()
                break
            except:
                time.sleep(0.01)  # wait 10ms
                pass  # retry
        cmd = '%s kill %s' % (self.docker, name)
#         rc = os.system(cmd)
        rc = system_like_exec(cmd, verbose=verbose)
        assert(rc == 0)
        p.wait()

    def run_registry(self, verbose=True):
        name = 'registry_bench_%d' % (random.randint(1, 1000000))
        cmd = '%s run --name=%s -p %d:%d ' % (self.docker,
                                              name, REGISTRY_PORT, 5000)
        cmd += '-e GUNICORN_OPTS=["--preload"] '
        cmd += '%sregistry' % self.registry
        if verbose:
            print(cmd)
            p_stdout = None
        else:
            p_stdout = subprocess.DEVNULL
        p = subprocess.Popen(cmd, shell=True, stderr=subprocess.STDOUT,
                             stdout=p_stdout)
        while True:
            try:
                req = urlreq.urlopen('http://localhost:%d' % REGISTRY_PORT)
                if verbose:
                    print((req.read().strip()))
                req.close()
                break
            except:
                time.sleep(0.01)  # wait 10ms
                pass  # retry
        cmd = '%s kill %s' % (self.docker, name)
#         rc = os.system(cmd)
        rc = system_like_exec(cmd, verbose=verbose)
        assert(rc == 0)
        p.wait()

    def run(self, bench, verbose=True):
        name = bench.name
        if name in BenchRunner.ECHO_HELLO:
            self.run_echo_hello(repo=name, verbose=verbose)
        elif name in BenchRunner.CMD_ARG:
            self.run_cmd_arg(repo=name, runargs=BenchRunner.CMD_ARG[name], verbose=verbose)
        elif name in BenchRunner.CMD_ARG_WAIT:
            self.run_cmd_arg_wait(
                repo=name, runargs=BenchRunner.CMD_ARG_WAIT[name], verbose=verbose)
        elif name in BenchRunner.CMD_STDIN:
            self.run_cmd_stdin(repo=name, runargs=BenchRunner.CMD_STDIN[name], verbose=verbose)
        elif name in BenchRunner.CUSTOM:
            fn = BenchRunner.__dict__[BenchRunner.CUSTOM[name]]
            fn(self, verbose=verbose)
        else:
            print(('Unknown bench: ' + name))
            exit(1)

    def pull(self, bench, verbose=True):
        cmd = '%s pull %s%s' % (self.docker, self.registry, bench.name)
#         rc = os.system(cmd)
        rc = system_like_exec(cmd, verbose=verbose)
        assert(rc == 0)

    def push(self, bench, verbose=True, to2=False):
        cmd = None
        if to2:
            cmd = '%s push %s%s' % (self.docker, self.registry2, bench.name)
        else:
            cmd = '%s push %s%s' % (self.docker, self.registry, bench.name)
#         rc = os.system(cmd)
        rc = system_like_exec(cmd, verbose=verbose)
        assert(rc == 0)

    def tag(self, bench, verbose=True):
        cmd = '%s tag %s%s %s%s' % (self.docker,
                                    self.registry, bench.name,
                                    self.registry2, bench.name)
#         rc = os.system(cmd)
        rc = system_like_exec(cmd, verbose=verbose)
        assert(rc == 0)

    def operation(self, op, bench, verbose=True):
        if op == 'run':
            self.run(bench, verbose=verbose)
        elif op == 'pull':
            self.pull(bench, verbose=verbose)
        elif op == 'push':
            self.push(bench, verbose=verbose)
        elif op == 'tag':
            self.tag(bench, verbose=verbose)
        elif op == 'move':
            self.pull(bench, verbose=verbose)
            self.tag(bench, verbose=verbose)
            self.push(bench, verbose=verbose, to2=True)
        else:
            print(('Unknown operation: ' + op))
            exit(1)


def list_bench(as_json=False):
    if as_json:
        print((json.dumps([b.__dict__ for b in list(BenchRunner.ALL.values())])))
    else:
        template = '%-16s\t%-20s'
        print((template % ('CATEGORY', 'NAME')))
        for b in sorted(list(BenchRunner.ALL.values()), key=lambda b: (b.category, b.name)):
            print((template % (b.category, b.name)))


def clean_containers(docker='docker', verbose=False):
    cmd = docker + ' ps -aq'
    p = subprocess.Popen(cmd, shell=True, stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
    p.wait()
    buf = p.stdout.read()
    ctrs = buf.decode().strip().split('\n')
    ctrs = list(filter(lambda x: len(x) > 0, ctrs))
    if len(ctrs) > 0:
        cmd = docker + ' rm ' + ' '.join(ctrs)
        p_stdout = None
        p_stderr = None
        if not verbose:
            p_stdout = subprocess.DEVNULL
            p_stderr = subprocess.DEVNULL
#         p = subprocess.Popen(cmd, shell=True, stderr=subprocess.STDOUT, stdout=subprocess.PIPE)
        p = subprocess.Popen(cmd, shell=True, stderr=p_stdout, stdout=p_stderr)
        p.wait()


def clean_images(docker='docker', verbose=True):
    clean_containers(docker=docker, verbose=verbose)
    cmd = docker + ' image prune -af'
    p_stdout = None
    if not verbose:
        p_stdout = subprocess.DEVNULL
    p = subprocess.Popen(cmd, shell=True, stdout=p_stdout)
    p.wait()


def main():
    args = parser.parse_args()
    t = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    tstr = t.strftime("%Y-%m-%d-%H-%M-%S")
    assert((args.trace_file is None and args.trace_dir is None) or
           (args.trace_file is not None and args.trace_dir is not None))

    if args.list:
        list_bench()
        exit(0)

    if args.list_json:
        list_bench(as_json=True)
        exit(0)

    if args.clean != 'none':
        clean_images(docker=args.docker, verbose=args.verbose)

    benches = []
    for bench in args.benchmarks.split(','):
        if bench == 'all':
            benches = list(BenchRunner.ALL.values())
            break
        else:
            benches.append(BenchRunner.ALL[bench])

    kvargs = {}
    if args.docker:
        kvargs['docker'] = args.docker

    kvargs['registry'] = args.registry

    if args.registry2:
        kvargs['registry2'] = args.registry2
    print(kvargs)
    outpath = args.out
    if args.add_time_postfix:
        outpath += '.{}'.format(tstr)
    if args.verbose:
        print('docker:   ', args.docker)
        print('op:       ', args.op)
        print('outpath:  ', outpath)
        print('clean:    ', args.clean)
        print('registry: ', args.registry)
        print('registry2:', args.registry2)
    # run benchmarks
    runner = BenchRunner(**kvargs)
    with open(outpath, 'w') as f:
        print("#", ' '.join(sys.argv), file=f)
        for bench in benches:
            if args.clean == 'each':
                clean_images(docker=args.docker, verbose=args.verbose)
            if args.verbose:
                print("start {}".format(bench.repo))
            start = time.time()
            runner.operation(args.op, bench, verbose=args.verbose)
            elapsed = time.time() - start
            row = {'repo': bench.repo, 'category': bench.category, 'clean_policy': args.clean, 'bench': bench.name, 'op': args.op, 'elapsed': elapsed, 'runtime': args.docker, 'start_time': tstr}
            js = json.dumps(row)
            if args.trace_file is not None:
                src = args.trace_file
                dst = os.path.join(args.trace_dir, bench.repo + ".trace")
                shutil.copy2(src, dst)
                row['trace'] = dst
            print(js)
            print(js, file=f)


if __name__ == '__main__':
    main()
    exit(0)

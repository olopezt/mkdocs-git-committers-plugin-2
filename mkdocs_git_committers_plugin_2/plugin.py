import os
import sys
import logging
from pprint import pprint
from timeit import default_timer as timer
from datetime import datetime, timedelta

from mkdocs import utils as mkdocs_utils
from mkdocs.config import config_options, Config
from mkdocs.plugins import BasePlugin

from git import Repo, Commit
import requests, json
import time
import hashlib
import re

LOG = logging.getLogger("mkdocs.plugins." + __name__)

class GitCommittersPlugin(BasePlugin):

    config_scheme = (
        ('enterprise_hostname', config_options.Type(str, default='')),
        ('repository', config_options.Type(str, default='')),
        ('branch', config_options.Type(str, default='master')),
        ('docs_path', config_options.Type(str, default='docs/')),
        ('token', config_options.Type(str, default='')),
        ('enabled', config_options.Type(bool, default=True)),
        ('cache_dir', config_options.Type(str, default='.cache/plugin/git-committers')),
    )

    def __init__(self):
        self.total_time = 0
        self.branch = 'master'
        self.enabled = True
        self.authors = dict()

    def on_config(self, config):
        self.enabled = self.config['enabled']
        if not self.enabled:
            LOG.info("git-committers plugin DISABLED")
            return config

        LOG.info("git-committers plugin ENABLED")
        if not self.config['token'] and 'MKDOCS_GIT_COMMITTERS_APIKEY' in os.environ:
            self.config['token'] = os.environ['MKDOCS_GIT_COMMITTERS_APIKEY']

        if self.config['token'] and self.config['token'] != '':
            self.auth_header = {'Authorization': 'token ' + self.config['token'] }
        else:
            LOG.warning("no git token provided and MKDOCS_GIT_COMMITTERS_APIKEY environment variable is not defined")
        if self.config['enterprise_hostname'] and self.config['enterprise_hostname'] != '':
            self.apiendpoint = "https://" + self.config['enterprise_hostname'] + "/api/graphql"
        else:
            self.apiendpoint = "https://api.github.com/graphql"
        self.localrepo = Repo(".")
        self.branch = self.config['branch']
        return config

    def get_gituser_info(self, query):
        if not hasattr(self, 'auth_header'):
            # No auth token provided: return now
            return None
        r = requests.post(url=self.apiendpoint, json=query, headers=self.auth_header)
        res = r.json()
        json_formatted_str = json.dumps(res, indent=2)
        LOG.debug("Json: " + json_formatted_str)
        if r.status_code == 200:
            if res.get('data'):
                if res['data']['search']['edges']:
                    info = res['data']['search']['edges'][0]['node']
                    if info:
                        return {'login':info['login'], \
                                'name':info['name'], \
                                'url':info['url'], \
                                'avatar':info['url']+".png" }
                    else:
                        return None
                else:
                    return None
            else:
                LOG.warning("Error from GitHub GraphQL call: " + res['errors'][0]['message'])
                return None
        else:
            return None

    def githubAuthorInfoFromUserOrEmail(self, githubUsername, githubEmail):
        if (githubUsername not in self.authors and githubEmail not in self.authors):            
            # Guard
            if githubUsername is None and githubEmail is None:
                return None

            # Define queries
            info = None
            if githubEmail is not None:
                LOG.debug("Looking for email " + githubEmail)
                searchByEmailQuery = """
                    search(type:USER, query: "in:email %s", first:1) { 
                        edges { node { ... on User { login name url  } } }
                    }
                """ % (githubEmail)
                query = { 'query': ' {' + searchByEmailQuery + ' }' }
                info = self.get_gituser_info(query)

                # Assume that the email was wrong and look for it as user
                if info is None:
                    LOG.debug("Looking for user " + githubEmail)
                    searchByEmailQuery = """
                        search(type:USER, query: "in:user %s", first:1) { 
                            edges { node { ... on User { login name url  } } }
                        }
                    """ % (githubEmail)
                    query = { 'query': ' {' + searchByEmailQuery + ' }' }
                    info = self.get_gituser_info(query)


            if githubUsername is not None and info is None:
                LOG.debug("Looking for user " + githubUsername)
                searchByUserQuery = """
                    search(type:USER, query: "in:user %s", first:1) { 
                        edges { node { ... on User { login name url  } } }
                    }
                """ % (githubUsername)
                query = { 'query': ' {' + searchByUserQuery + ' }' }
                info = self.get_gituser_info(query)

            # Search
            if info:
                LOG.debug("      Found!")
                if (githubEmail is not None):
                    LOG.debug("Registered " + githubEmail + " as " + info['login'])
                    author_id = githubEmail
                elif (githubUsername is not None):
                    LOG.debug("Registered " + githubUsername + " as " + info['login'])
                    author_id = githubUsername
                self.authors[author_id] = info
            else:
                LOG.debug("Not found for user " + githubUsername + " and email " + githubEmail)
                return None, None
        else:
            if (githubEmail in self.authors):
                author_id = githubEmail
            if (githubUsername in self.authors):
                author_id = githubUsername
            info = self.authors[author_id]
        return author_id, info


    def get_git_info(self, path, page):
        last_commit_date = ""
        unique_authors = []
        seen_authors = [] 
        LOG.debug("get_git_info for " + path)

        # Add contributors from commit info
        for c in Commit.iter_items(self.localrepo, self.localrepo.head, path):
            c.author.email = c.author.email.lower()
            # Clean up the email address
            c.author.email = re.sub('\d*\+', '', c.author.email.replace("@users.noreply.github.com", ""))

            author_id = ""
            if not last_commit_date:
                # Use the last commit and get the date
                last_commit_date = time.strftime("%Y-%m-%d", time.gmtime(c.authored_date))
            author_id, info = self.githubAuthorInfoFromUserOrEmail(c.author.name, c.author.email)
            if info is not None:
                if (author_id not in seen_authors):
                    seen_authors.append(author_id)
                    unique_authors.append(self.authors[author_id])


        # Add contributors from metadata
        if 'contributors' in page.meta:
            users = page.meta['contributors'].split(',')
            for author_name in users:
                author_id, info = self.githubAuthorInfoFromUserOrEmail(author_name, None)
                if info is None:
                    LOG.debug("Info for " + author_name + " not found")
                if info is not None:
                    LOG.debug("Info for " + author_name + " found")
                    if (author_id not in seen_authors):
                        seen_authors.append(author_id)
                        unique_authors.append(self.authors[author_id])

        # Return list
        LOG.debug("Contributors for page " + path + ": " + str(unique_authors))
        return unique_authors, last_commit_date

    def on_page_context(self, context, page, config, nav):
        context['committers'] = []
        if not self.enabled:
            return context
        start = timer()
        git_path = self.config['docs_path'] + page.file.src_path
        authors, last_commit_date = self.get_git_info(git_path, page)
        if authors:
            context['committers'] = authors
        if last_commit_date:
            context['last_commit_date'] = last_commit_date
        end = timer()
        self.total_time += (end - start)

        return context

    def on_post_build(self, config):
        LOG.info("git-committers: saving authors cache file")
        json_data = json.dumps(self.authors)
        os.makedirs(self.config['cache_dir'], exist_ok=True)
        f = open(self.config['cache_dir'] + "/authors.json", "w")
        f.write(json_data)
        f.close()

    def on_pre_build(self, config):
        if os.path.exists(self.config['cache_dir'] + "/authors.json"):
            LOG.info("git-committers: loading authors cache file")
            f = open(self.config['cache_dir'] + "/authors.json", "r")
            self.authors = json.loads(f.read())
            f.close()

from collections import namedtuple
import os
import re
import sys
from typing import Any, Optional

from bs4 import BeautifulSoup
import click
import requests
from transmission_rpc import Client


OptionalString = Optional[str]
PageType = requests.models.Response


def eprint(*args, **kwargs):
    """Print to standard error"""
    print(*args, file=sys.stderr, **kwargs)


class UserInputWasCancelled(Exception):
    """Should be raised when the user cancels an interactive input session"""


def get_from_user(named_thing, parse_from_string, choices):
    """Try to get the named thing as the result of `parse_from_string` from the user
    TODO extract this into a common python package or something like that as this is generally useful stuff that I shouldn't just copy paste...
    """
    value = None
    if isinstance(choices, tuple):
        choices_in_parentheses = choices
    else:
        comma_separated_list = ','.join([str(choice) for choice in choices])
        choices_in_parentheses = "({})".format(comma_separated_list)
    prompt = "{} {}: ".format(named_thing, choices_in_parentheses)
    while value is None:
        try:
            value = input(prompt)
        except KeyboardInterrupt:
            raise UserInputWasCancelled() from None
        try:
            value = parse_from_string(value)
        except (TypeError, ValueError):
            value = None
        if value is None:
            eprint("please specify a valid {} (or hit ctrl+c)".format(named_thing))
        else:
            if value not in choices:
                eprint("'{}' is outside of the possible choices: {}".format(value, choices_in_parentheses))
                value = None
    return value


class Session:
    """A session abstraction to capture all our web dependencies in one place"""

    __HEADER = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
                      " (KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36"
    }

    def __init__(self, server_url):
        self.__server_url = server_url
        self.__session = None

    def request(
            self, link: OptionalString = None, use_headers: bool = True,
            method: str = "get", data: Any = None) -> PageType:
        """Request the download of the link using the live session and return
        the HTML response
        Raise an exception if the final response code isn't 200 (OK).
        The `link` can be None to just send the request to the `server_url`,
        otherwise it must be a sub-link i.e. `server_url/link`.
        If `method` is not a valid session method, AttributeError will be
        raised
        """
        session_method = getattr(self.__session, method)

        link_url = self.url(link)

        params = {}
        if use_headers:
            params["headers"] = self.__HEADER
        if data is not None:
            params["data"] = data

        response = session_method(link_url, **params)
        response.raise_for_status()

        return response

    def url(self, link: OptionalString = None):
        server_url = self.__server_url
        return server_url if link is None else "{}/{}".format(server_url, link)

    def start_session(self):
        self.__session = requests.Session()

    def close_session(self):
        if self.__session is not None:
            self.__session.__exit__()


class nCore:
    __SERVER_URL = "https://ncore.cc"
    __LOGIN_LINK = "login.php"
    __LOGIN_NAME_FIELD = "nev"
    __LOGIN_PASSWORD_FIELD = "pass"
    __LOGOUT_LINK_PATTERN = "exit.php"
    __SEARCH_LINK = "torrents.php"
    __SEARCH_PATTERN_FIELD = "mire"

    def __init__(self, name, password):
        self.__name = name
        self.__password = password
        self.__session = Session(self.__SERVER_URL)
        self.__logged_in = False
        self.__dynamic_logout_link = None
        self.__login_form = {
            "set_lang": "hu",
            "submitted": '1',
        }
        self.__search_form = {
            "miben": "name",
            "tipus": "all_own",
            "submit.x": "34",
            "submit.y": "7",
            "tags": "",
        }

    def __enter__(self):
        try:
            self.__session.start_session()

            self.__login_form[self.__LOGIN_NAME_FIELD] = self.__name
            self.__login_form[self.__LOGIN_PASSWORD_FIELD] = self.__password

            print("Logging in... ", end="")
            response = self.__session.request(
                link=self.__LOGIN_LINK, method="post", data=self.__login_form
            )
            print("We are in :)")
            self.__logged_in = True
            self.__dynamic_logout_link = self.__parse_dynamic_logout_link(
                    response, self.__LOGOUT_LINK_PATTERN)

            return self
        except Exception:
            self.__exit__(*sys.exc_info())
            raise

    def __parse_dynamic_logout_link(self, page, pattern):
        soup = BeautifulSoup(page.text, "html.parser")
        for link_tag in soup.find_all('a'):
            link = link_tag.attrs["href"]
            if pattern in link:
                print("The found logout link is '{}'".format(link))
                return link
        raise RuntimeError("Logout link was not found!")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.__logged_in:
            assert self.__dynamic_logout_link is not None
            self.__session.request(self.__dynamic_logout_link)
            self.logged_in = False
            print("Logged out! :)")

        self.__session.close_session()

    @staticmethod
    def __find_result_text(result):
        return result.a.attrs["title"]

    def find(self, pattern, batch_mode):
        """Try to find and return a single torrent URL for `pattern`.

        The URL is returned if there is a single match.
        If multiple torrents match the `pattern` the result depends on the value of `batch_mode`:
            * True -> None, because we cannot decide in a non-interactive execution
            * False -> the choosen URL, the user is asked to choose one.
        """
        print("looking for '{}'...".format(pattern))
        self.__search_form[self.__SEARCH_PATTERN_FIELD] = pattern
        response = self.__session.request(
            link=self.__SEARCH_LINK, method="post", data=self.__search_form
        )
        soup = BeautifulSoup(response.text, "html.parser")
        results = soup.find_all("div", {"class": "torrent_txt"})
        num_results = len(results)
        if num_results > 1:
            eprint("multiple results:")
            for (index, result) in enumerate(results):
                eprint("{}. {}".format(index, self.__find_result_text(result)))
            if batch_mode:
                eprint("batch mode means we skip this one")
                choice = None
            else:
                result_index_range = range(num_results)
                try:
                    choice = get_from_user("index", int, choices=result_index_range)
                    choice = results[choice]
                except UserInputWasCancelled:
                    print("\nOK, skipped")
                    choice = None
        elif num_results == 1:
            choice = results[0]
            print("single result: '{}'".format(self.__find_result_text(choice)))
        else:
            eprint("no result")
            choice = None

        if choice is None:
            url = None
        else:
            raw_link = choice.a.attrs["href"]
            if match := re.search(r"id=(?P<torrent_id>\d+)", raw_link):
                torrent_id = int(match.group("torrent_id"))
            else:
                eprint("Failed to find an id in this raw_link: '{}'".format(raw_link))
                torrent_id = None

            if torrent_id is None:
                url = None
            else:
                torrent_link = "ajax.php?action=torrent_drop&id={}".format(torrent_id)
                torrent_response = self.__session.request(link=torrent_link, method="get")
                torrent_soup = BeautifulSoup(torrent_response.text, "html.parser")
                relative_link = torrent_soup.a.attrs["href"]
                url = self.__session.url(relative_link)

        return url


Data = namedtuple("Data", "torrent_name, absolute_path")


def find_untracked_data(data_dir, existing_torrents):
    """Return the untracked `Data` points from `data_dir`, excluding the `existing_torrents`"""
    torrent_names = [t.name for t in existing_torrents]
    untracked_data = []
    for (dir_path, dir_names, file_names) in os.walk(data_dir):
        if dir_names:
            for torrent_name in torrent_names:
                if torrent_name in dir_names:
                    dir_names.remove(torrent_name)
            dir_names.sort(key=lambda dir_name: os.stat(os.path.join(dir_path, dir_name)).st_mtime)
        if any([True for file_name in file_names if ".nfo" in file_name]):
            untracked_data.append(Data(torrent_name=os.path.basename(dir_path),
                                       absolute_path=dir_path))
    return untracked_data


@click.command()
@click.option("-d", "--data-dir", type=click.Path(exists=True, file_okay=False, dir_okay=True,
                                                  resolve_path=True, readable=True),
              required=True)
@click.option("-u", "--ncore-user", prompt=True)
@click.option("-p", "--ncore-password", prompt=True, hide_input=True)
@click.option("-t", "--transmission-user", prompt=True)
@click.option("-y", "--transmission-password", prompt=True, hide_input=True)
@click.option("-b", "--batch-mode", is_flag=True)
def restore(ncore_user, ncore_password, transmission_user, transmission_password, data_dir,
            batch_mode):
    """Restore all your torrents in transmission"""
    with nCore(ncore_user, ncore_password) as ncore:
        torrent_client = Client(username=transmission_user, password=transmission_password)

        existing_torrents = torrent_client.get_torrents()
        untracked_data = find_untracked_data(data_dir, existing_torrents)

        unavailable = []
        for (torrent_name, absolute_path) in untracked_data:
            torrent_url = ncore.find(torrent_name, batch_mode)
            if torrent_url is None:
                unavailable.append(torrent_name)
            else:
                absolute_parent_dir_path = os.path.dirname(absolute_path)
                print("add torrent:\n    '{}'".format(torrent_url))
                print("for local data:\n    '{}'".format(absolute_path))
                torrent = torrent_client.add_torrent(torrent_url, download_dir=absolute_parent_dir_path)

                # torrent.files() returns an empty list... :/
                files = torrent_client.get_files(torrent.id)[torrent.id]
                files_unwanted = []
                for (index, torrent_file) in enumerate(files):
                    absolute_file_path = os.path.join(absolute_parent_dir_path, torrent_file.name)
                    if not os.path.isfile(absolute_file_path):
                        files_unwanted.append(index)

                if files_unwanted:
                    # do not download anything more than what's already there
                    # this aims to prevent downloading unwanted samples or renamed files with their
                    # original names
                    torrent_client.change_torrent(torrent.id, files_unwanted=files_unwanted)

                files = torrent_client.get_files(torrent.id)[torrent.id]
                print("selected files:")
                for torrent_file in files:
                    if torrent_file.selected:
                        print("    * {}".format(torrent_file.name))
            print("=========================================================================")

        if unavailable:
            print("Failed to find the source for these items:")
            for torrent_name in unavailable:
                print("* {}".format(torrent_name))

if __name__ == "__main__":
    restore()


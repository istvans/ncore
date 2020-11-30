import sys
from typing import Any, Optional

from bs4 import BeautifulSoup
import click
import requests
from transmission_rpc import Client


OptionalString = Optional[str]
PageType = requests.models.Response


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

        server_url = self.__server_url
        link_url = server_url if link is None else "{}/{}".format(server_url, link)

        params = {}
        if use_headers:
            params["headers"] = self.__HEADER
        if data is not None:
            params["data"] = data

        response = session_method(link_url, **params)
        response.raise_for_status()

        return response

    def start_session(self):
        self.__session = requests.Session()

    def close_session(self):
        if self.__session is not None:
            self.__session.__exit__()


class nCore:
    __SERVER_URL = "https://ncore.cc"
    __LOGIN_LINK = "login.php"
    __SEARCH_LINK = "torrents.php"
    __LOGIN_FORM = {
        "set_lang": "hu",
        "submitted": '1',
    }
    __LOGIN_NAME_FIELD = "nev"
    __LOGIN_PASSWORD_FIELD = "pass"
    __LOGOUT_LINK_PATTERN = "exit.php"

    def __init__(self, name, password):
        self.__name = name
        self.__password = password
        self.__session = Session(self.__SERVER_URL)
        self.__logged_in = False
        self.__dynamic_logout_link = None

    def __enter__(self):
        try:
            self.__session.start_session()

            self.__LOGIN_FORM[self.__LOGIN_NAME_FIELD] = self.__name
            self.__LOGIN_FORM[self.__LOGIN_PASSWORD_FIELD] = self.__password

            print("Logging in... ", end="")
            response = self.__session.request(
                link=self.__LOGIN_LINK, method="post", data=self.__LOGIN_FORM
            )
            print("We are in :)")
            self.__logged_in = True
            self.__dynamic_logout_link = self.__parse_dynamic_logout_link(
                    response, self.__LOGOUT_LINK_PATTERN)
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

    def find(self, pattern, batch_mode):
        """Try to find a single torrent for `pattern`, download and return it

        The closest match is returned unless `batch_mode` is False.
        If `batch_mode` is False, the user needs to confirm each torrent.
        """
        return None


def find_local_folders(existing_torrents):
    """Return the potential torrent folders excluding the `existing_torrents`"""
    return []


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
        folders = find_local_folders(existing_torrents)

        unavailable = []
        for folder in folders:
            torrent = ncore.find(folder, batch_mode)
            if torrent is None:
                unavailable.append(folder)
            else:
                torrent_client.add_torrent(torrent, download_dir=folder)

        if unavailable:
            print("Failed to find the source for these items:")
            for folder in unavailable:
                print("* {}".format(folder))

if __name__ == "__main__":
    restore()


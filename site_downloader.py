from __future__ import print_function
import os
import sys
import re
import io
import collections
import datetime
import bs4
from bs4 import BeautifulSoup, SoupStrainer
import requests
from threading import Thread
import copy
import time
import logging
import traceback

try:   # Python 3
    from urllib.request import urlopen
    from urllib.parse import urljoin
    from urllib.error import HTTPError
except ImportError:   # Python 2
    from urllib import urlopen
    from urlparse import urljoin

    # Create dummy class to make exception handling easier.
    class HTTPError(Exception):
        pass

# Root exception class, not thrown directly.
class SiteDownloaderError(Exception):
    pass

class LogicError(SiteDownloaderError):
    pass
class SetupError(SiteDownloaderError):
    pass
class HTTPConnectError(SiteDownloaderError):
    pass
class HTTPRequestError(SiteDownloaderError):
    pass
class PageDetailsError(SiteDownloaderError):
    pass
class FileExistsError(SiteDownloaderError):
    pass
class WriteError(SiteDownloaderError):
    pass
class WindowsDelayedWriteError(SiteDownloaderError):
    pass


g_logger = None
g_userAgent = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36'

PROGRAM_NAME = 'SiteDownloader'
SPEED_TEST = False
SPEED_TEST_MAKES_FILES = True


def SetupLogger():
    global g_logger
    g_logger = logging.getLogger(PROGRAM_NAME)
    g_logger.setLevel(logging.DEBUG)

    handler = logging.FileHandler('output.log')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    g_logger.addHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    g_logger.addHandler(handler)

def LogDebug(*args):
    g_logger.debug(' '.join([ToStr(arg) for arg in args]))

def LogInfo(*args):
    g_logger.info(' '.join([ToStr(arg) for arg in args]))

def LogWarning(*args):
    g_logger.warning(' '.join([ToStr(arg) for arg in args]))

def LogError(*args):
    g_logger.error(' '.join([ToStr(arg) for arg in args]))

def LogCritical(*args):
    g_logger.critical(' '.join([ToStr(arg) for arg in args]))

SetupLogger()

def GetUserAgent():
    return g_userAgent

def SetUserAgent(userAgent):
    global g_userAgent
    g_userAgent = userAgent


class UrlInfo(object):
    def __init__(self, plugin, category, displayName, url, fileSavePath, bFile):
        self.plugin = plugin
        self.category = category
        self.displayName = displayName
        self.url = url
        self.fileSavePath = fileSavePath
        self.bFile = bFile

def ToStr(obj):
    try:
        return unicode(obj)
    except NameError:   # Python 3
        return str(obj)

def IsStr(obj):
    try:
        return isinstance(obj, unicode) or isinstance(obj, str)
    except NameError:   # Python 3
        return isinstance(obj, str)

def RemoveListDuplicates(lst):
    return list(collections.OrderedDict.fromkeys(lst))

# Note: For simplicity's sake, this includes any subdomains (e.g. info.blog.site.com).
# The reason for this is the difficulty in intelligently parsing out second-level
# domains, such as .co.uk.
def GetDomain(url, bReturnEndPos=False):
    doubleSlashPos = url.find('//')
    domainStartPos = doubleSlashPos + len('//') if doubleSlashPos != -1 else 0

    slashPos = url.find('/', domainStartPos)

    domainEndPos = None
    if slashPos == -1:
        domainEndPos = len(url)
    else:
        domainEndPos = slashPos

    domain = url[domainStartPos : domainEndPos]

    if domain.startswith('www.'):
        domain = domain[len('www.'):]

    if bReturnEndPos:
        return (domain, domainEndPos)
    else:
        return domain

# Note that this fails to diagnose images that are, say, followed by an expiration tag
# in the URL.
def IsImageURL(url):
    fileExt = os.path.splitext(url)[1]
    return fileExt in ['.png', '.jpg', '.jpeg', '.jpe', '.jiff', '.gif', '.svg', '.bmp', '.tif', '.tiff']

class SiteDownloader(object):
    MAX_WORKER_THREADS = 10

    def __init__(self, rootDir=None, urlList=None, bSingleThread=False):
        self.bRunning = True
        self.plugins = []

        # Threads used to download items. In single-thread mode, we only create one
        # worker thread at a time, and it never actually runs; we use it as a wrapper to
        # contain data and code for a URL to be processed.
        self.threads = []

        self.urlItemSet = set()
        # Contains either raw URLs, or UrlInfo objects.
        self.urlItems = []

        self.rootDir = rootDir
        if SPEED_TEST:
            self.rootDir = os.path.join(self.rootDir, 'speedtest')

        self.failedImages = []
        self.failedUrls = []

        self.bSingleThread = bSingleThread

        if urlList is not None:
            self.AddUrls(urlList)

    def AddUrls(self, urlList):
        if self.rootDir is None:
            raise SetupError('No root dir set')

        for url in urlList:
            if url in self.urlItemSet:
                continue

            self.urlItemSet.add(url)
            self.urlItems.append(url)

    def CheckDeadThreads(self):
        # Note that if we're running in single-thread mode, any fake threads we've
        # created as data processing objects will not be alive.
        deadThreads = [t for t in self.threads if not t.is_alive()]
        self.threads = [t for t in self.threads if t not in deadThreads]

        for t in deadThreads:
            errorPrefix = 'For URL: ' + t.GetUrl() + '\n'

            if isinstance(t.rval, Exception):
                # Note that we can get a HTTPError or IOError as a result of a urlopen()
                # failure, but we'll rely on other code to wrap such calls and won't check
                # for them here.
                if isinstance(t.rval, HTTPConnectError) or isinstance(t.rval, HTTPRequestError):
                    if IsStr(t.urlItemObj):
                        LogError(errorPrefix + 'Error retrieving page')
                    else:
                        if IsImageURL(t.urlItemObj.url):
                            if not t.urlItemObj.url in self.failedImages:
                                LogError(errorPrefix + 'Error retrieving image')
                                self.failedImages.append(t.urlItemObj.url)
                        else:
                            if not t.urlItemObj.url in self.failedUrls:
                                LogError(errorPrefix + 'Error retrieving data')
                                self.failedUrls.append(t.urlItemObj.url)
                elif isinstance(t.rval, WriteError):
                    LogError(errorPrefix + 'Error:', ToStr(t.rval))
                elif isinstance(t.rval, PageDetailsError):
                    LogError(errorPrefix + 'Problem when parsing page:', ToStr(t.rval))
                elif isinstance(t.rval, FileExistsError):
                    LogError(errorPrefix + 'File already exists:', ToStr(t.rval))
                elif isinstance(t.rval, WindowsDelayedWriteError):
                    LogError(errorPrefix + 'Failed to download file')
                elif isinstance(t.rval, LogicError):
                    LogError(errorPrefix + 'Error:', ToStr(t.rval))
                else:
                    try:
                        LogError(errorPrefix + 'Raising exception from thread:', t.rval.traceback)
                    except AttributeError:
                        LogError(errorPrefix + 'Raising exception from thread:')
                    raise t.rval.__class__(ToStr(t.rval))
            else:
                if t.rval is None:
                    LogError(errorPrefix + 'Error: Got nothing from parsing page')
                    return

                LogDebug('Got', len(t.rval), 'items from parsing:', t.GetUrl())
                newUrlItems = t.rval

                # Note that a URL and a UrlItem wrapping that URL do not cause a clash,
                # nor should they; standard procedure after getting a URL is to wrap it
                # in a UrlItem.

                newUrlItems = [urlItem for urlItem in newUrlItems if urlItem not in self.urlItemSet]

                newUrlItems = RemoveListDuplicates(newUrlItems)

                self.urlItems = newUrlItems + self.urlItems

                for urlItem in newUrlItems:
                    if IsStr(urlItem):
                        self.urlItemSet.add(urlItem)
                    else:
                        self.urlItemSet.add(urlItem.url)

            g_timeoutHandler.UpdateDomainConnectFailCount(t.domainConnectFailCount)

    def RunMainThread(self):
        if self.bSingleThread:
            while len(self.urlItems) > 0:
                urlItem = self.urlItems[0]
                self.urlItems = self.urlItems[1:]

                fakeThread = DownloadThread(urlItem, copy.copy(self.plugins), copy.copy(self.rootDir))
                self.threads.append(fakeThread)

                # Run the code that the worker thread would normally run, but run
                # that code in the main thread.
                fakeThread.ProcessUrl()
                self.CheckDeadThreads()
        else:
            while self.bRunning:
                # TODO in frontend, set bRunning to false when program is ready to exit.
                self.CheckDeadThreads()

                if len(self.urlItems) == 0 and len(self.threads) == 0:
                    break

                while len(self.urlItems) > 0:
                    if len(self.threads) < self.MAX_WORKER_THREADS:
                        urlItem = self.urlItems[0]
                        self.urlItems = self.urlItems[1:]

                        thread = DownloadThread(copy.copy(urlItem), copy.copy(self.plugins), copy.copy(self.rootDir))
                        self.threads.append(thread)
                        thread.start()
                    else:
                        prevThreadNum = len(self.threads)
                        self.CheckDeadThreads()
                        if len(self.threads) >= prevThreadNum:
                            break

                time.sleep(0.01)

            for thread in self.threads:
                thread.join()

        LogInfo('Exiting main thread')


class TimeoutHandler(object):
    PAGE_FILE_EXTENSIONS = ['.html', '.htm', '.php', '.asp']
    NON_IMAGE_DOWNLOAD_FILE_EXTENSIONS = ['.css', '.js']

    def __init__(self, urlList=None, rootDir=None, defaultConnectTimeout=10, defaultReadTimeout=10, connectAttempts=3):
        self.defaultConnectTimeout = defaultConnectTimeout
        self.defaultReadTimeout = defaultReadTimeout
        self.connectAttempts = connectAttempts
        self.domainConnectFailCount = collections.defaultdict(int)

    # Return the (connectTimeout, readTimeout) that we want to use when trying to get the
    # file at the URL. Note that the URL timeout handler tracks the domain (or, actually,
    # subdomain) associated with each URL we access. If URLs from a subdomain often fails
    # connect timeout checks, the timeout handler decreases the connect timeout used for
    # URLs from that subdomain. This will probably result in the connect timeout failure
    # count getting ever higher -- since we now wait less long to find out whether a
    # connection "failed".
    def GetUrlTimeouts(self, fileUrl):
        domain, domainEndPos = GetDomain(fileUrl, bReturnEndPos=True)

        fileExt = None

        # Note: The if condition is to avoid a situation where we treat the top-level
        # domain as part of the extension.
        if domainEndPos < len(fileUrl):
            fileExt = os.path.splitext(fileUrl)[1]

            if len(fileExt) == 0:
                fileExt = None
            else:
                questionMarkPos = fileExt.find('?')
                if questionMarkPos != -1:
                    fileExt = fileExt[:questionMarkPos]

        if fileExt is None or fileExt in self.PAGE_FILE_EXTENSIONS:
            return (self.defaultConnectTimeout, self.defaultReadTimeout)
        elif fileExt in self.NON_IMAGE_DOWNLOAD_FILE_EXTENSIONS:
            return (self.defaultConnectTimeout, self.defaultReadTimeout)
        else:
            return self.GetImageUrlTimeouts(fileUrl, domain, fileExt, self.domainConnectFailCount[domain])

    def GetImageUrlTimeouts(self, fileUrl, domain, fileExt, domainConnectFailCount):
        if domainConnectFailCount > 5:
            return (self.defaultConnectTimeout // 2, self.defaultReadTimeout)
        if domainConnectFailCount > 20:
            return (self.defaultConnectTimeout // 4, self.defaultReadTimeout)
        else:
            return (self.defaultConnectTimeout, self.defaultReadTimeout)

    # Note that this function is not thread-safe. However, it is currently called only
    # from the main thread.
    def UpdateDomainConnectFailCount(self, domainConnectFailCount):
        for domain, connectFailCount in domainConnectFailCount.items():
            self.domainConnectFailCount[domain] += connectFailCount


g_timeoutHandler = TimeoutHandler()


class SiteDownloaderPlugin(object):
    def ProcessorName(self):
        return ''

    def GetPageRelevance(self, url):
        return 0

    def GetLoginCredentials(self, url):
        return None

    def GetPageCategory(self, url, soup):
        return None

    # Returns newUrlItems
    def ProcessUserAddedUrl(self, url):
        return []

    # Returns (newUrlItems, pageSoupToWrite, pageSoupToWriteFilePath)
    def ProcessUrlInfo(self, urlInfo):
        return [], None, None

    # Returns a requests.Response object which contains the result of a POST or GET
    # request to a URL.
    def GetPage(self, url, data=None, headers=None, cookies=None, loginCredentials=None):
        client = requests.session()
        client.headers = requests.utils.default_headers()

        client.headers.update({'User-Agent': GetUserAgent()})

        if headers is not None:
            client.headers.update(headers)

        assembledKwargs = {}

        if data is not None:
            assembledKwargs['data'] = data

        if cookies is not None:
            assembledKwargs['cookies'] = cookies

        if loginCredentials is not None:
            assembledKwargs.update(loginCredentials)

        r = None
        for attempt in range(g_timeoutHandler.connectAttempts):
            try:
                if data is not None:
                    r = client.post(url, timeout=g_timeoutHandler.GetUrlTimeouts(url), **assembledKwargs)
                else:
                    r = client.get(url, timeout=g_timeoutHandler.GetUrlTimeouts(url), **assembledKwargs)

                if r.status_code != 200:
                    continue

                break
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError):
                if attempt == g_timeoutHandler.connectAttempts - 1:
                    raise HTTPConnectError('Request failed')

        if r is None or r.status_code != 200:
            raise HTTPRequestError('Request failed')
        return r

    def GetSoup(self, html, soupStrainer=None):
        # Limit the type of tags we parse, to speed up BeautifulSoup's parsing.
        try:
            if soupStrainer is not None:
                return BeautifulSoup(html, features='lxml', parse_only=strainer)
            else:
                return BeautifulSoup(html, features='lxml')
        except bs4.FeatureNotFound:
            LogInfo('lxml parser not available, using default html parser')
            if soupStrainer is not None:
                return BeautifulSoup(html, features='html.parser', parse_only=strainer)
            else:
                return BeautifulSoup(html, features='html.parser')

    # Note that we don't allow ampersands, which can form valid filenames but can confuse
    # browsers since they expect ampersands to appear in HTML as "&amp;".
    def FilenameChar(self, ch):
        return ch.isalnum() or ch in ['.', '_', '-', '=']

    # Return simplified filenames that we use when saving files. We do this to make sure
    # that we don't get weird filenames that might work on, say, Linux, but not on
    # Windows.
    def UsableFilename(self, filename):
        return ''.join(ch for ch in filename if self.FilenameChar(ch))


class DownloadThread(Thread):
    def __init__(self, urlItemObj, plugins, rootDir):
        self.urlItemObj = urlItemObj
        self.plugins = plugins
        self.rootDir = rootDir
        self.rval = None
        self.domainConnectFailCount = collections.defaultdict(int)
        super(DownloadThread, self).__init__()

    def run(self):
        self.ProcessUrl()
        LogDebug('Thread ending for URL', self.GetUrl())

    def GetUrl(self):
        if IsStr(self.urlItemObj):
            return self.urlItemObj
        else:
            return self.urlItemObj.url

    def ProcessUrl(self):
        bStrObj = IsStr(self.urlItemObj)

        usePlugin = None

        if bStrObj:
            url = self.urlItemObj

            highestRelevance = 0
            for plugin in self.plugins:
                relevance = plugin.GetPageRelevance(url)
                if relevance > highestRelevance:
                    highestRelevance = relevance
                    usePlugin = plugin
        else:
            url = self.urlItemObj.url

            if self.urlItemObj.plugin is None:
                raise LogicError('UrlItem lacks plugin: ' + self.urlItemObj)
            usePlugin = self.urlItemObj.plugin

        if usePlugin is None:
            self.rval = PageDetailsError('No plugin to process URL')
            return

        newUrlItems = None

        if bStrObj:
            LogInfo('Processing user-added URL', url)

            try:
                newUrlItems = usePlugin.ProcessUserAddedUrl(url)
            except Exception as error:
                error.traceback = traceback.format_exc()
                self.rval = error
                return
        else:
            urlInfo = self.urlItemObj

            if urlInfo.category is not None and len(urlInfo.category) > 0:
                LogInfo('Processing', urlInfo.category, urlInfo.url)
            else:
                LogInfo('Processing', urlInfo.displayName, urlInfo.url)

            if urlInfo.bFile:
                try:
                    self.DownloadFile(urlInfo.url, os.path.join(self.rootDir, urlInfo.fileSavePath), urlInfo.plugin.GetLoginCredentials(urlInfo.url))
                    newUrlItems = []
                except Exception as error:
                    error.traceback = traceback.format_exc()
                    self.rval = error
                    return
            else:
                try:
                    newUrlItems, soup, pageFilePath = usePlugin.ProcessUrlInfo(urlInfo)

                    if (soup is not None or pageFilePath is not None) and (soup is None or pageFilePath is None):
                        raise LogicError('Failed to get proper info to save page')

                    if soup is not None and pageFilePath is not None:
                        if not SPEED_TEST or SPEED_TEST_MAKES_FILES:
                            # Save page as file.

                            pageSavePath = os.path.join(self.rootDir, pageFilePath)

                            if os.path.exists(pageSavePath):
                                # Note that we don't throw an exception here, so that
                                # we instead return the list of new URL items we got.
                                LogError('Error: For URL:', urlInfo.url, '\nPage file already exists:', pageSavePath)
                            else:
                                saveDirPath = os.path.dirname(pageSavePath)
                                try:
                                    if not os.path.exists(saveDirPath):
                                        os.makedirs(saveDirPath)

                                    with io.open(pageSavePath, 'w', encoding='utf-8') as outFile:
                                        outFile.write(ToStr(soup))
                                except (OSError, IOError):
                                    raise WriteError('Unable to create file: ' + pageSavePath)
                except Exception as error:
                    error.traceback = traceback.format_exc()
                    self.rval = error
                    return

        if self.rval is None:
            self.rval = newUrlItems

    def DownloadFile(self, fileUrl, savePath, loginCredentials=None):
        LogInfo('Downloading', fileUrl, 'to', savePath)

        if SPEED_TEST and not SPEED_TEST_MAKES_FILES:
            return

        if os.path.exists(savePath):
            raise FileExistsError(savePath)

        startTime = datetime.datetime.now()
        try:
            client = requests.session()
            client.headers = requests.utils.default_headers()

            client.headers.update({'User-Agent': GetUserAgent()})

            if loginCredentials is not None:
                r = requests.get(fileUrl, stream=True, timeout=g_timeoutHandler.GetUrlTimeouts(fileUrl), **loginCredentials)
            else:
                r = client.get(fileUrl, stream=True, timeout=g_timeoutHandler.GetUrlTimeouts(fileUrl))
        except requests.exceptions.ConnectTimeout:
            self.domainConnectFailCount[GetDomain(fileUrl)] += 1
            raise HTTPConnectError()
        except requests.exceptions.RequestException:
            raise HTTPConnectError()
        endTime = datetime.datetime.now()

        try:
            fileType = r.headers['Content-Type']
            if fileType == 'text/html':
                raise HTTPRequestError('Got HTML page instead of file')

            fileSize = int(r.headers['Content-Length'])
        except (KeyError, TypeError):
            fileSize = None
            LogWarning('Warning: For URL:', fileUrl, '\nNo way of verifying file size')

        if r.status_code == 200:
            try:
                saveDirPath = os.path.dirname(savePath)
                if not os.path.exists(saveDirPath):
                    os.makedirs(saveDirPath)

                startTime = datetime.datetime.now()
                with open(savePath, 'wb') as outFile:
                    for chunk in r.iter_content(chunk_size=1024):
                        if chunk:   # Don't write keep-alive chunks
                            outFile.write(chunk)

                LogDebug('Finished writing', fileUrl)

                if fileSize is not None:
                    gotFileSize = os.path.getsize(savePath)
                    if gotFileSize != fileSize:
                        raise HTTPRequestError('File size mismatch: expected ' + str(fileSize) + ', got' + str(gotFileSize))
                endTime = datetime.datetime.now()
            except FileNotFoundError:
                # It's possible to get this error (yes, when writing to a new file) as a
                # result of calling open() on Windows. This can happen if there is a
                # "Delayed Write Error", where "Windows was unable to save all the data
                # for the file". This is very rare, but I have observed it happening with
                # this program on Windows 7. When it did, Windows announced it via a popup
                # on the system tray.

                # We will also get this error for filenames that exceed the maximum
                # allowed file path length. We could deal with this, but we would also
                # then need to modify every reference to this file in all CSS and
                # JavaScript files. So... a possible TODO.

                raise WindowsDelayedWriteError('Unable to create file: ' + savePath)
            except (OSError, IOError):
                raise WriteError('Unable to create file: ' + savePath)
        else:
            raise HTTPRequestError('Request failed')

        LogDebug('Done writing file for URL', fileUrl)

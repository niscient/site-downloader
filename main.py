"""
SiteDownloader
Requirements: requests, beautifulsoup4
Optional: lxml (for faster parsing)

Notes:
-I use requests since urllib.urlretrieve has problems with some types of files,
 notably many GIFs and some JPGs.
-It's a pain to support both BeautifulSoup 3 and 4, so 4 is required. The syntax it
 uses to access attributes is much better.

Future major improvements:
-Create a GUI frontend and use it to keep track of download progress and possible errors
 for each url item.
-Support a wider variety of vBulletin URL and CSS formats.
-Support vBulletin forums, in addition to individual threads. This will require
 implementing a system for starting at the last page of a forum and working towards the
 start. As we are considering pages, we must account for the possibility that users
 could post a lot of new threads, thus making it hard for the program to tell if it has
 really considered all possible thread URLs (since newly created threads push old threads
 back onto pages that we might have parsed already). To account for this: For each page
 considered, we must first re-check the previous page we checked (which is numerically
 the "next" page) and see if there are any unfamiliar thread URLs on it now; if so, it's
 possible that a lot of new threads have appeared since we originally processed this
 page. If ALL the thread URLs are unfamiliar, we may need to check the page we processed
 before this one (the "next" page after this); etc.

Future minor improvements:
-Load plugins dynamically.
-Download JS files. Examples of tags:
 <script type="text/javascript" src="file.js"></script>
 <script language="javascript" src="file.js" />
-Scan images stored in 'source'->'srcset'; merge this with the existing 'img'->'src' code.
-Scan inline CSS for 'background'; merge this with the existing 'background-image' code.
-Scan internal and external css for 'background-image' and 'background'. Possibly use
 cssutils to assist with this, or just use the url() regex matcher I already have.
 Alternatively, I could do what Chrome seems to do, and insert the HTML base tag value
 as a prefix to any relative url() paths found in internal/external/inline styles.
"""

from __future__ import print_function
import os
import sys
import datetime
import argparse
import configparser
from site_downloader import SiteDownloader, LogDebug, LogError, PageDetailsError, SetupError, HTTPConnectError, HTTPRequestError, PROGRAM_NAME, SetUserAgent

PLUGIN_DIR = 'plugins'

def main(bSpeedTest=False):
    argParser = argparse.ArgumentParser()
    argParser.add_argument('root', help='Root directory to store downloaded files')
    argParser.add_argument('file_with_urls', help='Text file containing URLs to download')
    args = argParser.parse_args()

    rootDir = args.root
    if not os.path.isdir(rootDir):
            raise SetupError('Invalid root dir: "' + rootDir + '"')

    inFilePath = args.file_with_urls
    if not os.path.isfile(inFilePath):
        raise SetupError('URL list file doesn\'t exist: "' + inFilePath + '"')

    dl = SiteDownloader(rootDir=rootDir, bSingleThread=True)

    if not os.path.isdir(PLUGIN_DIR):
        raise SetupError("Couldn't find '" + PLUGIN_DIR + "' directory")

    for fileName in os.listdir(PLUGIN_DIR):
        moduleName, fileExt = os.path.splitext(fileName)
        if fileExt == '.py' and moduleName != '__init__':
            try:
                moduleFullName = PLUGIN_DIR + '.' + moduleName
                __import__(moduleFullName)
                module = sys.modules[moduleFullName]
                dl.plugins.append(module.PluginClass())
            except (ImportError, KeyError, AttributeError):
                raise SetupError('Unable to import plugin module: ' + moduleName)

    if len(dl.plugins) == 0:
        raise SetupError("Couldn't find any plugins to load")

    LogDebug('Processing URL list')

    urlList = []

    with open(inFilePath, 'r') as inFile:
        for line in inFile:
            url = line.rstrip()
            urlList.append(url)

    dl.AddUrls(urlList)

    dl.RunMainThread()

if __name__ == '__main__':
    try:
        config = configparser.ConfigParser()
        config.read('settings.ini')
        try:
            SetUserAgent(config[PROGRAM_NAME]['user_agent'])
        except (configparser.MissingSectionHeaderError, KeyError):
            pass

        bSpeedTest = '--test' in sys.argv
        startTime = datetime.datetime.now()
        main(bSpeedTest=bSpeedTest)
        endTime = datetime.datetime.now()

        if bSpeedTest:
            LogInfo('Took', (endTime - startTime).total_seconds(), 'seconds')
    except PageDetailsError as error:
        LogError('Error:', error)
    except SetupError as error:
        LogError('Error:', error)
    except (HTTPConnectError, HTTPRequestError) as error:
        LogError('Error:', error)
# Processor for threads from vBulletin forums

import os
import datetime
import re
from site_downloader import SiteDownloader, SiteDownloaderPlugin, SPEED_TEST, LogDebug, LogError, PageDetailsError, HTTPError, urlopen, urljoin, UrlInfo

class VBulletinForumProcessor(SiteDownloaderPlugin):
    def __init__(self, bDownloadFiles=True, bChangeFilePaths=True):
        self.bDownloadFiles = bDownloadFiles
        self.bChangeFilePaths = bChangeFilePaths

    def ProcessorName(self):
        return 'vBulletin'

    def GetPageRelevance(self, url):
        return (100 if url.find('/showthread.php?') != -1 else 0)

    # For later use.
    def ParseCategoryTag(self, tagValue):
        return ''

    def GetPageCategory(self, url, soup):
        return ''

    def ProcessUserAddedUrl(self, url):
        newUrlItems = []

        # Get details on the forum thread.

        match = re.search('(.*)(/showthread.php\?)(\d+-.*)(/page\d+)', url)
        bUsedAltRegex = False
        if not match:
            # Try without page identifier.
            match = re.search('(.*)(/showthread.php\?)(\d+-.*)', url)
            bUsedAltRegex = True
            if not match:
                raise PageDetailsError("URL isn't a valid first page of a thread")

        try:
            # TODO replace this with self.GetPage(url) and do timeout checking.
            response = urlopen(url)
        except (HTTPError, IOError):
            raise PageDetailsError('Invalid URL')
        if response.getcode() != 200:
            raise PageDetailsError('Failed to read page')

        urlIntro = match.group(1)
        preMainName = match.group(2)   # For later use with other vBulletin URL formats.
        mainName = match.group(3)

        if bUsedAltRegex:
            pageInfo = ''
        else:
            pageInfo = match.group(4) if match.group(4) is not None else ''

        usableMainName = self.UsableFilename(mainName)
        if len(mainName) == 0 or usableMainName != mainName:
            raise PageDetailsError('Failed to parse main page name')

        startTime = datetime.datetime.now()
        soup = self.GetSoup(response)
        endTime = datetime.datetime.now()

        if SPEED_TEST:
            LogDebug('Soup initial', (endTime - startTime).total_seconds(), 'seconds')

        category = self.GetPageCategory(url, soup)

        # Find out the range of pages that exist for this forum thread.

        bFoundLastPageTag = False

        # This tag format only exists on desktop browsers, but that's fine, this will
        # never be run in mobile.
        for lastPageTag in soup.findAll('a', {'class': 'popupctrl'}):
            if lastPageTag.string is None:
                continue

            match = re.match('Page 1 of (\d+)', lastPageTag.string)
            if match:
                lastPage = int(match.group(1))
                bFoundLastPageTag = True
                break

        if not bFoundLastPageTag:
            LogWarning('Warning: For URL: ' + url + "\nCouldn't find last page tag")
            lastPage = 1

        # Add the pages to the processing queue.

        for page in range(1, lastPage + 1):
            pageUrl = '{}{}{}/page{}'.format(urlIntro, preMainName, mainName, page)

            if category is not None and len(category) > 0:
                pageFilename = '{}-{}-{}{}'.format(usableMainName, category, page)
            else:
                pageFilename = '{}-{}'.format(usableMainName, page)

            bDownloadPageOnly = not self.bDownloadFiles
            newUrlItems.append(UrlInfo(plugin=self, category=category, displayName=pageFilename, url=pageUrl, fileSavePath=pageFilename, bFile=bDownloadPageOnly))

        LogDebug('---------------------------')
        return newUrlItems

    def ProcessUrlInfo(self, urlInfo):
        newUrls = set()
        newUrlItems = []

        url = urlInfo.url

        saveDirName = os.path.basename(urlInfo.fileSavePath) + '_files'

        startTime = datetime.datetime.now()
        soup = self.GetSoup(urlopen(url))
        endTime = datetime.datetime.now()
        if SPEED_TEST:
            LogDebug('--page soup', (endTime - startTime).total_seconds(), 'seconds')

        startTime = datetime.datetime.now()

        if self.bChangeFilePaths:
            # We need to overwrite the base tag, or all relative paths will use it.
            baseTag = soup.find('base')
            if baseTag is not None:
                if 'href' in baseTag.attrs:
                    baseTag.attrs['href'] = '.'

        sectionStartTime = datetime.datetime.now()

        for imageTag in soup.findAll('img'):
            imageUrl = urljoin(url, imageTag['src'])
            filename = self.UsableFilename(imageTag['src'].split('/')[-1])
            imageSavePath = os.path.join(saveDirName, filename)

            if imageUrl not in newUrls:
                newUrlInfo = UrlInfo(plugin=urlInfo.plugin, category=urlInfo.category, displayName=filename, url=imageUrl, fileSavePath=imageSavePath, bFile=True)
                newUrlItems.append(newUrlInfo)
                newUrls.add(imageUrl)

            if self.bChangeFilePaths:
                if 'src' in imageTag.attrs:
                    imageSaveRelPath = imageSavePath
                    imageTag.attrs['src'] = imageSaveRelPath

        if SPEED_TEST:
            LogDebug('---scanned images', (datetime.datetime.now() - sectionStartTime).total_seconds())
        sectionStartTime = datetime.datetime.now()

        for divTag in soup.findAll(style=True):
            if divTag['style'].find('background-image') != -1:
                style = divTag['style']
                pattern = 'background-image\w*:url\([\'"]?([^\'"]*)[\'"]?\)'

                match = re.search(pattern, style)
                if match:
                    divUrl = match.group(1)
                    divUrl = urljoin(url, divUrl)
                    filename = self.UsableFilename(divUrl.split('/')[-1])
                    divSavePath = os.path.join(saveDirName, filename)

                    newUrls.append(UrlInfo(plugin=urlInfo.plugin, category=urlInfo.category, displayName=filename, url=divUrl, fileSavePath=divSavePath, bFile=True))

                    if self.bChangeFilePaths:
                        divSaveRelPath = os.path.join(saveDirName, filename)
                        divSaveRelPath = divSaveRelPath.replace('\\', '\\\\')
                        divTag['style'] = style[:match.start(1)] + divSaveRelPath + style[match.end(1):]

        if SPEED_TEST:
            LogDebug('---scanned divs', (datetime.datetime.now() - sectionStartTime).total_seconds())
        sectionStartTime = datetime.datetime.now()

        for linkTag in soup.findAll('link'):
            bIsCSS = False
            if 'type' in linkTag.attrs:
                if linkTag.attrs['type'] == 'text/css':
                    bIsCSS = True

            if bIsCSS:
                if 'href' in linkTag.attrs:
                    linkUrl = urljoin(url, linkTag.attrs['href'])
                    linkUrl = linkUrl.replace('&amp;', '&')

                    filename = self.UsableFilename(linkTag.attrs['href'].split('/')[-1])
                    linkSavePath = os.path.join(saveDirName, filename)

                    if linkUrl not in newUrls:
                        newUrlInfo = UrlInfo(plugin=urlInfo.plugin, category=urlInfo.category, displayName=filename, url=linkUrl, fileSavePath=linkSavePath, bFile=True)
                        newUrlItems.append(newUrlInfo)
                        newUrls.add(linkUrl)

                    if self.bChangeFilePaths:
                        linkSaveRelPath = os.path.join(saveDirName, filename)
                        linkTag.attrs['href'] = linkSaveRelPath

        if SPEED_TEST:
            LogDebug('---scanned links', (datetime.datetime.now() - sectionStartTime).total_seconds())
        sectionStartTime = datetime.datetime.now()

        if SPEED_TEST:
            LogDebug('---outputting file', (datetime.datetime.now() - sectionStartTime).total_seconds())

        endTime = datetime.datetime.now()
        if SPEED_TEST:
            pageScanTime = (endTime - startTime).total_seconds()
            LogDebug('Page scan {:.3}'.format(pageScanTime), 'seconds')

        return newUrlItems, soup, urlInfo.fileSavePath + '.html'

PluginClass = VBulletinForumProcessor
# SiteDownloader

Download manager which downloads websites. It scans website URLs, finds out the URLs of relevant linked pages and and resources (images, CSS files, etc), and downloads all this information. Only works with a certain group of sites. You can extend it to add functionality for new sites using site-specific plugins.

Uses Requests for HTTP requests and BeautifulSoup for HTML parsing.

Run program by passing a text file with URLs as an argument. You also have to specify the root directory where you want files saved.

Example:

```
python main.py "C:\Downloads" file_with_urls.txt
```

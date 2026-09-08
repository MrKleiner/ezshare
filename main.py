import sys
import os
import struct
import time
import tarfile
import zipfile
import shutil

from pathlib import Path
from io import BytesIO
from datetime import datetime
from socketserver import _SocketWriter
from pathlib import PurePosixPath

from bs4 import BeautifulSoup as jquery

from jag import htservice
from jag.mimes import BASE_MIMES
from jag.mimes import BASE_MIMES_SIGNED



# ==============================
#           CONSTANTS
# ==============================

USE_SENDFILE = hasattr(os, 'sendfile')

STATIC = Path(__file__).parent / 'static'

HTTP_404 = (Path(__file__).parent / 'static' / '404.html').read_bytes()

DL_REDIRECT = ''

VID_EXT = (
	'.mp4',
	'.webm',
	'.mov',
	'.avi',
)

SIZE_UNITS_PPRINT = (
	'',
	'K',
	'M',
	'G',
	'T',
	'P',
	'E',
	'Z',
)

# 
# Extend mimes
# 
BASE_MIMES['vmix'] = 'application/xml'
BASE_MIMES_SIGNED['.vmix'] = 'application/xml'




# ==============================
#           FUNCTIONS
# ==============================

def stream_dir_as_archive(tgt_dir, send_func):
	# Open the tar archive in write mode
	with tarfile.open(fileobj=SKTChunkPipe(send_func), mode='w|') as tar:
		# Add each file to the archive
		for file_path in [f for f in tgt_dir.rglob('*') if f.is_file()]:
			tar.add(
				file_path,
				arcname=os.path.relpath(file_path, str(tgt_dir))
			)

	# time.sleep(5)
	send_func(b'0\r\n\r\n')

	# sockfile = None


def sizeof_fmt(num, suffix='B'):
	for unit in SIZE_UNITS_PPRINT:
		if abs(num) < 1024.0:
			# return f'{num:3.1f}{unit}{suffix}'
			return (f'{num:3.1f}', f'{unit}{suffix}')
		num /= 1024.0
	return (f'{num:.1f}', 'Yi')


def _list_dir(tgt_dir, root_dir, root_symbolic_name='EZShare'):
	html_doc = jquery(
		(STATIC / 'dir_list_template.html').read_bytes(),
		'html.parser'
	)

	item_list = sorted(
		[e for e in tgt_dir.glob('*')],
		key=lambda i: int(i.is_file())
	)

	item_tplate = (STATIC / 'list_item_template.html').read_bytes()

	display_path = tgt_dir.relative_to(root_dir).as_posix()
	if display_path == '.':
		html_doc.select_one('body > h1').string = f'EZShare/'
	else:
		html_doc.select_one('body > h1').string = f'EZShare/{display_path}/'

	# todo: security?
	item_path = '/' + str(tgt_dir.relative_to(root_dir).parent).strip()
	if root_symbolic_name:
		item_path = ''.join([
			'/',
			root_symbolic_name.strip(' /'),
			'/',
			str(tgt_dir.relative_to(root_dir).parent).strip(),
		])

	tag = jquery(item_tplate, 'html.parser')

	tag.select_one('.prefix_icon.dl_item')['go_up'] = True
	tag.select_one('.item_href')['href'] = item_path
	tag.select_one('.item_href').string = '../'
	tag.select_one('.item_actions .dl_item')['href'] = None

	tag.select_one('.item_actions .item_type_icon')['go_up'] = True

	html_doc.body.ul.append(tag)


	for item in item_list:
		# TODO: WHAT THE FUCK IS WORNG WITH HTML TODAY???
		# WHY DOES IT ALWAYS USE ABSOLUTE PATHS ???!!!!
		# FUCK YOUUUUUUUUUUUUU
		item_path = '/' + str(item.relative_to(root_dir)).strip()
		if root_symbolic_name:
			item_path = ''.join([
				'/',
				root_symbolic_name.strip(' /'),
				'/',
				str(item.relative_to(root_dir)).strip(),
			])
			# print('bro?', root_symbolic_name, item_path)

		tag = jquery(item_tplate, 'html.parser')

		tag.select_one('.item_href')['href'] = item_path
		tag.select_one('.item_href').string = item.name + ('/' * item.is_dir())
		tag.select_one('.item_actions .dl_item')['href'] = item_path + '?dl=1'

		itype_attr = 'folder' if item.is_dir() else 'file'
		tag.select_one('.item_actions .item_type_icon')[itype_attr] = True

		tag.select_one('.item_info.mod_date').string = (
			# sizeof_fmt(item.stat().st_size).ljust(20, ' ')
			# + '    |    ' + 
			datetime.fromtimestamp(item.stat().st_mtime)
			.strftime('%Y-%m-%d %H:%M:%S')
		)

		if item.is_file():
			fsize = sizeof_fmt(item.stat().st_size)
			tag.select_one('.item_info.fsize [num]').string = fsize[0]
			tag.select_one('.item_info.fsize [txt]').string = fsize[1]

		html_doc.body.ul.append(tag)

	return html_doc.prettify().encode()


def list_dir(tgt_dir, root_dir, root_symbolic_name='EZShare'):
	html_doc = jquery(
		(STATIC / 'dir_list_template.html').read_bytes(),
		'html.parser'
	)

	files = []
	dirs = []

	for item_path in tgt_dir.glob('*'):
		if item_path.is_file():
			files.append(item_path)
		else:
			dirs.append(item_path)

	item_list = sorted(
		[e for e in tgt_dir.glob('*')],
		key=lambda i: int(i.is_file())
	)

	item_tplate = (STATIC / 'list_item_template.html').read_bytes()

	display_path = tgt_dir.relative_to(root_dir).as_posix()
	if display_path == '.':
		html_doc.select_one('body > h1').string = f'EZShare/'
	else:
		html_doc.select_one('body > h1').string = f'EZShare/{display_path}/'

	# todo: security?
	item_path = '/' + str(tgt_dir.relative_to(root_dir).parent).strip()
	if root_symbolic_name:
		item_path = ''.join([
			'/',
			root_symbolic_name.strip(' /'),
			'/',
			str(tgt_dir.relative_to(root_dir).parent).strip(),
		])

	tag = jquery(item_tplate, 'html.parser')

	tag.select_one('.prefix_icon.dl_item')['go_up'] = True
	tag.select_one('.item_href')['href'] = item_path
	tag.select_one('.item_href').string = '../'
	tag.select_one('.item_actions .dl_item')['href'] = None

	tag.select_one('.item_actions .item_type_icon')['go_up'] = True

	html_doc.body.ul.append(tag)


	for item in item_list:
		# TODO: WHAT THE FUCK IS WORNG WITH HTML TODAY???
		# WHY DOES IT ALWAYS USE ABSOLUTE PATHS ???!!!!
		# FUCK YOUUUUUUUUUUUUU
		item_path = '/' + str(item.relative_to(root_dir)).strip()
		if root_symbolic_name:
			item_path = ''.join([
				'/',
				root_symbolic_name.strip(' /'),
				'/',
				str(item.relative_to(root_dir)).strip(),
			])

		tag = jquery(item_tplate, 'html.parser')

		tag.select_one('.item_href')['href'] = item_path
		tag.select_one('.item_href').string = item.name + ('/' * item.is_dir())
		tag.select_one('.item_actions .dl_item')['href'] = item_path + '?dl=1'

		itype_attr = 'folder' if item.is_dir() else 'file'
		tag.select_one('.item_actions .item_type_icon')[itype_attr] = True

		item_stats = item.stat()

		tag.select_one('.item_info.mod_date').string = (
			# sizeof_fmt(item.stat().st_size).ljust(20, ' ')
			# + '    |    ' + 
			datetime.fromtimestamp(item_stats.st_mtime)
			.strftime('%Y-%m-%d %H:%M:%S')
		)

		tag.li['mtime'] = item_stats.st_mtime

		if item.is_file():
			tag.select_one('.item_href')['href'] += '?prv=1'
			fsize = sizeof_fmt(item_stats.st_size)
			tag.select_one('.item_info.fsize [num]').string = fsize[0]
			tag.select_one('.item_info.fsize [txt]').string = fsize[1]
			tag.li['isfile'] = 1
			tag.li['fsize'] = item_stats.st_size
		else:
			tag.li['isfile'] = 0

		html_doc.body.ul.append(tag)

	return html_doc.prettify().encode()


def dl_file(tgt_file, htrequest):
	tgt_file = Path(tgt_file)

	htrequest.send_headers_only({
		'Content-Type':        'application/octet-stream',
		'Content-Disposition': f'''attachment; filename="{tgt_file.name}"''',
		'Content-Length':      tgt_file.stat().st_size,
	})

	# todo: this is pornography
	tgt_wfile = _SocketWriter(htrequest.cl_con)

	with open(tgt_file, 'rb') as src_file:
		# htrequest.wfile.flush()

		if USE_SENDFILE:
			htrequest.cl_con.sendfile(src_file)
		else:
			with htrequest.cl_con.makefile('wb') as wfile:
				shutil.copyfileobj(src_file, wfile, 1024**2)
			# shutil.copyfileobj(src_file, tgt_wfile, 1024**2)


def parse_range_header(range_header):
	# Extract the start and end values from the Range header
	if not range_header.startswith('bytes='):
		# Invalid or unsupported Range header
		return 0, -1

	_, range_values = range_header.split('=')
	ranges = range_values.split(',')

	if len(ranges) > 1:
		# Currently only supporting a single range,
		# ignore additional ranges
		return 0, -1

	start, end = ranges[0].split('-')

	if start == '':
		# Suffix byte range
		start = -int(end)
		end = -1
	elif end == '':
		start, end = int(start), -1
	else:
		start, end = map(int, ranges[0].split('-'))

	return start, end







# ==============================
#         UTIL CLASSES
# ==============================

class EZSData:
	def __init__(
		self,
		root_dir=None,
		extra_mimes=None
	):
		self.root_dir = root_dir

		self.mimes = (
			BASE_MIMES |
			(extra_mimes or {})
		)
		self.mimes_prefixed = (
			BASE_MIMES_SIGNED |
			{('.'+k):v for k,v in (extra_mimes or {}).items()}
		)



class SKTChunkPipe(BytesIO):
	def __init__(self, send_func):
		self.send_func = send_func
		super().__init__()

	def write(self, data):
		# print('Writing...', len(data))
		# Send data over the socket
		self.send_func(
			f"""{hex(len(data)).lstrip('0x')}\r\n""".encode()
		)
		self.send_func(data)

		self.send_func(b'\r\n')

		return len(data)










# ==============================
#       PREVIEW CLASSES
# ==============================

class PreviewVideo:
	MAX_READ_SIZE = (1024**2) * 4

	EXT = (
		'.mp4',
		'.webm',
		'.m4v',
		'.ogv',
		'.mov',
	)

	def serve_partial_video(self):
		htrequest = self.htrequest

		start, end = parse_range_header(
			htrequest.headers.get('Range')
		)

		with open(self.fpath, 'rb') as tgt_buf:
			data_length = tgt_buf.seek(0, 2)
			tgt_buf.seek(0, 0)

			if end == -1:
				end = min(
					start + self.MAX_READ_SIZE - 1,
					data_length - 1
				)
			elif end > data_length - 1:
				end = data_length - 1

			if start >= data_length or start > end or start < 0:
				htrequest.response_code = 416
				htrequest.deny()
				return

			read_size = min(end - start + 1, self.MAX_READ_SIZE)

			tgt_buf.seek(start, 0)
			partial_content = tgt_buf.read(read_size)

		htrequest.response_code = 206
		htrequest.additive_headers['Content-Range'] = (
			f'bytes {start}-{end}/{data_length}'
		)

		htrequest.flush_bytes(
			partial_content,
			BASE_MIMES_SIGNED.get(self.fpath.suffix.lower())
		)

	def run(self, htrequest, fpath):
		self.htrequest = htrequest
		self.fpath = fpath

		if self.htrequest.headers.get('Range', '').count('-') != 1:
			with open(fpath, 'rb') as fbuf:
				htrequest.stream_buf(
					fbuf,
					BASE_MIMES_SIGNED.get(fpath.suffix.lower())
				)
			return

		self.serve_partial_video()



class PreviewImage:
	EXT = (
		'.png',
		'.jpg',
		'.jpeg',
		'.webp',
		'.apng',
		'.svg',
		'.avif',
		'.gif',
		'.bmp',
		'.ico',
		'.jxl',
	)

	MIMES = (
		('.jpg',  'image/jpeg'    ),
		('.jpeg', 'image/jpeg'    ),
		('.png',  'image/png'     ),
		('.webp', 'image/webp'    ),
		('.apng', 'image/apng'    ),
		('.svg',  'image/svg+xml' ),
		('.avif', 'image/avif'    ),
		('.gif',  'image/gif'     ),
		('.bmp',  'image/bmp'     ),
		('.ico',  'image/x-icon'  ),
		('.jxl',  'image/jxl'     ),
	)

	def run(self, htrequest, fpath):
		with open(fpath, 'rb') as fbuf:
			htrequest.stream_buf(
				fbuf,
				dict(self.MIMES).get(fpath.suffix.lower())
			)



class PreviewGTZIP:
	EXT = (
		'.gtzip',
	)

	def run(self, htrequest, fpath):
		with zipfile.ZipFile(fpath, 'r') as zf_buf:
			with zf_buf.open('thumbnail.png') as fbuf:
				htrequest.flush_bytes(
					fbuf.read(),
					'image/png',
				)



class PreviewZIP:
	EXT = (
		'.zip',
	)

	@staticmethod
	def build_zip_tree(zip_path):
		tree = {}

		with zipfile.ZipFile(zip_path, "r") as z:
			for info in z.infolist():
				if info.filename.endswith("/"):
					continue

				path = PurePosixPath(info.filename)
				parts = path.parts

				node = tree
				for part in parts[:-1]:
					node = node.setdefault(part, {})

				node[parts[-1]] = info.file_size

		return tree

	# Yes, recursion, BUT:
	# No FUCKING WAY there'd be an archive 1000 folders deep...
	@classmethod
	def render_tree(cls, node, prefix=''):
		lines = []

		items = sorted(node.items(), key=lambda item: (isinstance(item[1], dict), item[0].lower()))

		for i, (name, value) in enumerate(items):
			last = i == len(items) - 1
			branch = '└── ' if last else '├── '
			next_prefix = prefix + ('    ' if last else '│   ')

			if isinstance(value, dict):
				lines.append(f'{prefix}{branch}{name}/')
				lines.extend(cls.render_tree(value, next_prefix))
			else:
				name = name.ljust(40)
				size_num, size_text = sizeof_fmt(value)
				# lines.append(f"{prefix}{branch}{name} ({value} bytes)")
				lines.append(f'{prefix}{branch}{name} ({size_num} {size_text})')


		return lines

	def run(self, htrequest, fpath):
		htrequest.flush_bytes(
			'\n'.join(
				self.render_tree(
					self.build_zip_tree(fpath)
				)
			).encode(),
			'text/plain; charset=utf-8',
		)



# ==============================
#        SERVICE CLASSES
# ==============================

class EZSRoot:
	route = '/EZShare'

	def __init__(self, htrequest):
		self.htrequest = htrequest

	def run(self, htrequest):
		EZSMain(htrequest).run(htrequest)



class EZSMain:
	route = '/EZShare*>'

	PREVIEW_SERVERS = (
		PreviewVideo,
		PreviewImage,
		PreviewGTZIP,
		PreviewZIP,
	)

	def __init__(self, htrequest):
		self.htrequest = htrequest
		self.root_dir = htrequest.shared_data.root_dir

	def run(self, htrequest):
		tgt_path = (
			self.root_dir /
			Path(htrequest.path.replace('EZShare', '').strip('/'))
		).resolve()
		print('Requested path:', tgt_path)

		if not tgt_path.is_relative_to(self.root_dir) or not tgt_path.exists():
			htrequest.flush_bytes(
				HTTP_404,
				'text/html'
			)
			return

		need_dl = htrequest.query_params.get('dl') == '1'
		need_preview = htrequest.query_params.get('prv') == '1'
		is_dir = tgt_path.is_dir()
		path_suffix = tgt_path.suffix.lower()

		htrequest.additive_headers['Cache-Control'] = (
			'no-store'
		)

		# Streaming dirs as archives
		if is_dir and need_dl:
			htrequest.send_headers_only({
				'Transfer-Encoding': 'chunked',
				'Content-Type':      'application/octet-stream',
				'Content-Disposition': f'''attachment; filename="{tgt_path.name}.tar"'''
			})
			stream_dir_as_archive(tgt_path, htrequest.sendall)
			return

		# If not streaming and is dir - list it
		if is_dir:
			htrequest.additive_headers['Cache-Control'] = (
				f'public, max-age=10, immutable, must-revalidate'
			)
			htrequest.flush_bytes(
				list_dir(tgt_path, self.root_dir),
				'text/html'
			)
			return

		# Try serving a somewhat fancy preview
		if need_preview and not is_dir:
			# Try finding targeted preview strat
			for maker in self.PREVIEW_SERVERS:
				if path_suffix in maker.EXT:
					maker().run(htrequest, tgt_path)
					return

			# Resort to basics
			if path_suffix in BASE_MIMES_SIGNED:
				with open(tgt_path, 'rb') as fbuf:
					htrequest.stream_buf(
						fbuf,
						BASE_MIMES_SIGNED.get(path_suffix)
					)
				return

		# Resort to just downloading the file
		if tgt_path.is_file():
			dl_file(tgt_path, htrequest)



class EZSCdn:
	route = '/cdn/static*>'

	def __init__(self, htrequest):
		self.htrequest = htrequest
		self.mimes = htrequest.shared_data.mimes
		self.mimes_prefixed = htrequest.shared_data.mimes_prefixed

	def run(self, htrequest):
		tgt_path = (
			STATIC / Path(
				htrequest.adjusted_path
				.replace('cdn/static', '')
				.strip('/')
			)
		).resolve()

		print('fuckj', tgt_path)

		if not tgt_path.is_file():
			htrequest.flush_bytes(
				HTTP_404,
				'text/html'
			)
			return

		htrequest.additive_headers['Cache-Control'] = (
			f'public, max-age=9000, immutable, must-revalidate'
		)

		htrequest.flush_bytes(
			tgt_path.read_bytes(),
			self.mimes_prefixed[tgt_path.suffix.lower()]
		)



class EZSRedirect:
	route = '/'

	def __init__(self, htrequest):
		self.htrequest = htrequest

	def run(self, htrequest):
		self.htrequest.redirect('/EZShare', 308)



class EZSRedirectFromLegacy:
	handle_404 = True

	def __init__(self, htrequest):
		self.htrequest = htrequest

	def run(self, htrequest):
		htrequest.redirect(
			'/EZShare/' + htrequest.path.strip(' /').lstrip('EZShare'),
			301
		)



class EZShare:
	def __init__(
		self,
		tgt_port,
		tgt_root_path,
		extra_mimes=None
	):
		self.port = int(tgt_port)
		self.root_path = tgt_root_path
		self.extra_mimes = extra_mimes
		# EZSMain.ROOT_PATH = self.root_path
		# NAV_IDX[NAV_IDX.index(EZSMain)].ROOT_PATH = self.root_path

	def run(self):
		# htnav = ShareNav(Path(self.root_path))

		ezs_shared_data = EZSData(
			root_dir=self.root_path,
			extra_mimes=self.extra_mimes
		)

		http_server = htservice.MinHTTP(
			# htnav.route,
			htservice.Router(NAV_IDX).nav,
			tgt_port=self.port,
			shared_data=ezs_shared_data
		)
		http_server.run()

	def run_async_subp(self):
		import multiprocessing

		multiprocessing.Process(
			target=self.run
		).start()

	def run_async_th(self):
		import threading

		threading.Thread(
			target=self.run
		).start()


NAV_IDX = (
	EZSRedirectFromLegacy,
	EZSRedirect,
	EZSMain,
	EZSCdn,
)


def main():
	port, root_path = sys.argv[-1].split('=')

	ez_share = EZShare(port, root_path)
	ez_share.run()

	print('Launched HTTP server...')




if __name__ == '__main__':
	main()


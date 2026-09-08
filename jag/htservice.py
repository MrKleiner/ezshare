
"""
Simple multiprocessed HTTP server.
"""

from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor

import socket
import threading
import io
import sys
import time
import json
import multiprocessing
import random
import urllib

IS_WIN = sys.platform.startswith('win')

RSP_CODE_MAP = {
	# Information responses
	100: '100 Continue',
	101: '101 Switching Protocols',
	102: '102 Processing',
	103: '103 Early Hints',

	# Successful responses
	200: '200 OK',
	201: '201 Created',
	202: '202 Accepted',
	203: '203 Non-Authoritative Information',
	204: '204 No Content',
	205: '205 Reset Content',
	206: '206 Partial Content',
	207: '207 Multi-Status',
	208: '208 Already Reported',
	226: '226 IM Used',

	# Redirection messages
	300: '300 Multiple Choices',
	301: '301 Moved Permanently',
	302: '302 Found',
	303: '303 See Other',
	304: '304 Not Modified',
	307: '307 Temporary Redirect',
	308: '308 Permanent Redirect',

	# Client error responses
	400: '400 Bad Request',
	401: '401 Unauthorized',
	402: '402 Payment Required',
	403: '403 Forbidden',
	404: '404 Not Found',
	405: '405 Method Not Allowed',
	406: '406 Not Acceptable',
	407: '407 Proxy Authentication Required',
	408: '408 Request Timeout',
	409: '409 Conflict',
	410: '410 Gone',
	411: '411 Length Required',
	412: '412 Precondition Failed',
	413: '413 Payload Too Large',
	414: '414 URI Too Long',
	415: '415 Unsupported Media Type',
	416: '416 Range Not Satisfiable',
	417: '417 Expectation Failed',
	418: """418 I'm a teapot""",
	421: '421 Misdirected Request',
	422: '422 Unprocessable Content',
	423: '423 Locked',
	424: '424 Failed Dependency',
	425: '425 Too Early',
	426: '426 Upgrade Required',
	428: '428 Precondition Required',
	429: '429 Too Many Requests',
	431: '431 Request Header Fields Too Large',
	451: '451 Unavailable For Legal Reasons',

	# Server error responses
	500: '500 Internal Server Error',
	501: '501 Not Implemented',
	502: '502 Bad Gateway',
	503: '503 Service Unavailable',
	504: '504 Gateway Timeout',
	505: '505 HTTP Version Not Supported',
	506: '506 Variant Also Negotiates',
	507: '507 Insufficient Storage',
	508: '508 Loop Detected',
	510: '510 Not Extended',
	511: '511 Network Authentication Required',
}

def dbg_print(*args, **kwargs):
	if __debug__:
		print(*args, **kwargs)

class StopExecution(Exception):
	pass

class ExhaustedHTTPRequest(Exception):
	pass

class HeadersAlreadySent(Exception):
	pass


# fuck python
# fuck it very much. Retard
def print_exception(err):
	import traceback
	try:
		print(
			''.join(
				traceback.format_exception(
					type(err),
					err,
					err.__traceback__
				)
			)
		)
	except Exception as e:
		print(e)


class BodyStreamReader:
	def __init__(self, http_request):
		self.http_request = http_request
		self.readall = http_request.readall
		self.prog = int(http_request.headers.get('Content-Length', 0))
		self.payload_len = int(http_request.headers.get('Content-Length', 0))

	def __enter__(self):
		return self

	def __exit__(self, type, value, traceback):
		# todo: does this also has to be hex ?
		# self.sendall(b'0\r\n\r\n')
		return

	def read(self, tgt_read_size=4096):
		if self.payload_len:
			read_size = max(
				0,
				min(tgt_read_size, self.prog)
			)
			# chunk = self.readall(read_size)
			self.prog -= len(
				chunk := self.readall(read_size)
			)

			return chunk

		return self.readall(tgt_read_size)


class ChunkedStream:
	def __init__(self, http_request):
		# http_request.htsession.wfile.flush()
		self.http_request = http_request
		self.sendall = http_request.htsession.cl_con.sendall

	def __enter__(self):
		return self

	def __exit__(self, type, value, traceback):
		# todo: does this also has to be hex ?
		self.sendall(b'0\r\n\r\n')

	def send(self, data):
		# send the chunk size
		self.sendall(
			f"""{hex(len(data)).lstrip('0x')}\r\n""".encode()
		)
		# send the chunk itself
		self.sendall(data)
		# send separator
		self.sendall(b'\r\n')


class ByteRangeServer:
	CHUNK_SIZE = 4096

	def __init__(self, htrequest):
		self.htrequest = htrequest

	@staticmethod
	def parse_range_header(range_header):
		# Extract the start and end values from the Range header
		if not range_header.startswith('bytes='):
			# Invalid or unsupported Range header
			return 0, -1

		_, range_values = range_header.split('=')
		ranges = range_values.split(',')

		if len(ranges) > 1:
			# Currently only supporting a single range, ignore additional ranges
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

	def pipe_buffer(self, tgt_buf):
		start, end = self.parse_range_header(
			self.htrequest.headers.get('Range')
		)

		content_length = tgt_buf.seek(0, 2)
		tgt_buf.seek(0, 0)

		if end == -1:
			end = min(start + self.MAX_READ_SIZE - 1, content_length - 1)
		elif end > content_length - 1:
			end = content_length - 1

		if start >= content_length or start > end or start < 0:
			self.htrequest.deny('416 Requested Range Not Satisfiable')
			return

		read_size = min(end - start + 1, self.MAX_READ_SIZE)

		tgt_buf.seek(start, 0)

		# partial_content = tgt_buf.read(read_size)

		self.htrequest.response_code = '206 Partial Content'
		self.htrequest.additive_headers['Content-Range'] = (
			f'bytes {start}-{end}/{content_length}'
		)
		# self.htrequest.additive_headers['Content-Type'] = (
		# 	'text/plain'
		# )
		self.htrequest.send_headers_only()

		read = tgt_buf.read
		write = self.htrequest.sendall

		progress = 0
		while True:
			data = read(
				min(self.CHUNK_SIZE, read_size - progress)
			)
			if not data:
				break

			progress += len(data)
			write(data)

		# self.htrequest.flush_bytes(
		# 	partial_content,
		# 	BASE_MIMES_SIGNED.get(cl_abspath.suffix.lower(), 'text/plain')
		# )


class MinHTTPRequest:
	def __init__(
		self,
		hlist,
		htsession,
		shared_data=None,
		must_close=False
	):
		self.exhausted = False
		self.headers_sent = False

		self.must_close = must_close

		self.cl_con = htsession.cl_con

		self.shared_data = shared_data
		# self.session_data = htsession.session_data
		self.htsession = htsession

		self.rfile = htsession.rfile
		# self.wfile = htsession.wfile

		self.readall = htsession.rfile.read
		self.sendall = self.cl_con.sendall

		self.hlist = hlist
		self.method, self.path, self.protocol = self.hlist[0].split(' ')
		del self.hlist[0]

		parsed_url = urllib.parse.urlparse(self.path)

		self.query_params:dict = {
			k:(''.join(v)) for (k,v) in urllib.parse.parse_qs(parsed_url.query, True).items()
		}
		
		self._cookies = None

		self.path = urllib.parse.unquote(parsed_url.path)
		self.adjusted_path = None

		self.headers = {}

		self.additive_headers = {
			'Jag-S': self.htsession.session_id,
			'Jag-I': f'{self.htsession.served_requests}/{self.htsession.MAX_REQUESTS}',
		}

		self._response_code = '200 OK'

		for header in self.hlist:
			hkey, hval = header.split(': ')
			self.headers[hkey.strip()] = hval.strip()

		# print('Created HTTP Request class')

	@staticmethod
	def lock_skt_rw(method):
		def wrap(self, *args, **kwargs):
			if self.exhausted:
				raise ExhaustedHTTPRequest(
					f"""HTTP Request {self} is exhausted, """
					"""which means no data can be read/written, """
					f"""but '{method.__name__}' requires read/write."""
				)

			result = method(self, *args, **kwargs)

			self.exhausted = True
			self.lock_headers = True

			return result

		return wrap

	@staticmethod
	def lock_headers(method):
		def wrap(self, *args, **kwargs):
			if self.headers_sent:
				raise ExhaustedHTTPRequest(
					f"""HTTP headers for request {self} """
					"""have already been sent."""
				)

			result = method(self, *args, **kwargs)

			self.headers_sent = True

			return result

		return wrap

	@property
	def response_code(self):
		return self._response_code

	@response_code.setter
	def response_code(self, code):
		# mapped_code = RSP_CODE_MAP.get(code)

		# if mapped_code:
			# self._response_code = mapped_code
		# else:
			# self._response_code = code

		self._response_code = RSP_CODE_MAP.get(code, code)

	@property
	def cookies(self):
		if self._cookies != None:
			return self._cookies

		cookie_data = {}

		for header in self.hlist:
			hname, hdata = header.split(': ')
			hname = hname.lower().strip()

			if hname == 'cookie':
				for cookie in hdata.split(';'):
					cookie_name, cookie_val = cookie.split('=')
					cookie_data[cookie_name.strip()] = cookie_val.strip()

		self._cookies = cookie_data

		return self._cookies

	@property
	def session_data(self):
		return self.htsession.session_data

	@lock_skt_rw
	def deny(self, code=None, body_data=None):
		data = body_data or b'Bad Request'
		self.sendall(
			('HTTP/1.1' + RSP_CODE_MAP.get(code, '400 Bad Request') + '\r\n')
			.encode()
		)
		self.send_headers({
			'Server': 'EZShare',
			'Connection': 'Keep-Alive',
			'Content-Length': len(data),
		})
		self.sendall(b'\r\n')
		self.sendall(data)

	def send_headers(self, hdict):
		for hkey, hval in hdict.items():
			if self.must_close and hkey.lower() == 'connection':
				print('============= OVERWRITING CLOSE')
				hval = 'Close'
			self.sendall(f"""{str(hkey)}: {str(hval)}\r\n""".encode())

	@lock_headers
	def send_headers_only(self, hdict):
		self.sendall(f'HTTP/1.1 {self.response_code}\r\n'.encode())
		self.send_headers(hdict)
		self.send_headers(self.additive_headers)
		self.sendall(b'\r\n')

	@lock_skt_rw
	def flush_bytes(self, data, content_type='text/plain'):
		self.sendall(f'HTTP/1.1 {self.response_code}\r\n'.encode())
		self.send_headers(
			{
				'Server': 'JAG',
				'Content-Type': str(content_type),
				'Connection': 'Keep-Alive',
				'Content-Length': len(data),
			}
		)
		self.send_headers(self.additive_headers)
		self.sendall(b'\r\n')
		self.sendall(data)

	@lock_skt_rw
	def flush_json(self, data):
		self.flush_bytes(
			json.dumps(data).encode(),
			'application/json'
		)

	@lock_skt_rw
	def stream_chunks(self, content_type='text/plain'):
		self.send_headers_only({
			'Transfer-Encoding': 'chunked',
			'Connection': 'Keep-Alive',
			'Content-Type': str(content_type),
		})

		return ChunkedStream(self)

	@lock_skt_rw
	def _stream_buf(self, buf, content_type='application/octet-stream', chunksize=None):
		self.send_headers_only({
			'Server':         'JAG',
			'Connection':     'Keep-Alive',
			'Content-Type':   str(content_type),
			'Content-Length': buf.seek(0, 2),
		})

		buf.seek(0, 0)

		chunksize = chunksize or 1024**2

		while (chunk := buf.read(chunksize)):
			self.sendall(chunk)

	@lock_skt_rw
	def stream_buf(self, buf, content_type='application/octet-stream', chunksize=None):
		self.send_headers_only({
			'Server':         'JAG',
			'Connection':     'Keep-Alive',
			'Content-Type':   str(content_type),
			'Content-Length': buf.seek(0, 2),
		})

		buf.seek(0, 0)

		chunksize = chunksize or 1024**2

		# self.htsession.wfile.flush()

		while (chunk := buf.read(chunksize)):
			self.htsession.cl_con.sendall(chunk)

	@lock_skt_rw
	def serve_range(self, tgt_path=None, tgt_buf=None):
		if tgt_buf:
			ByteRangeServer(self).pipe_buffer(tgt_buf)
		else:
			with open(str(tgt_path), 'rb') as tgt_buf:
				ByteRangeServer(self).pipe_buffer(tgt_buf)

	def read_body(self, max_len=None):
		content_length = self.headers.get('Content-Length')
		if not content_length:
			self.deny(411)
			return

		try:
			content_length = int(content_length)
		except ValueError:
			self.deny(400)
			return

		if max_len and content_length > max_len:
			self.deny(413)
			return

		with self.read_body_stream() as stream:
			buf = io.BytesIO()
			while chunk := stream.read((1024**2)*10):
				buf.write(chunk)

		return buf.getvalue()

	def read_body_stream(self):
		return BodyStreamReader(self)

	@lock_skt_rw
	def redirect(self, tgt, code=307):
		self.response_code = code
		self.send_headers_only({
			'Location': tgt,
			'Content-Length': 2,
		})

		self.sendall(b'MV')



class HTTPSession:
	MAX_REQUESTS = 50
	MAX_LIFE = 10

	MAX_HEADER_BUF_SIZE = 1024*128

	def __init__(self, cl_con, callback, shared_data=None, pool_id=None):
		self.cl_con = cl_con
		self.callback = callback

		self.pool_id = str(pool_id)[0:8].ljust(8, ' ')
		self.session_id = str(random.random())[0:8].ljust(8, ' ')

		self.shared_data = shared_data
		self.session_data = None

		self.served_requests = 0

		self.rfile = cl_con.makefile('rb', newline=b'\r\n', buffering=0)
		# self.wfile = cl_con.makefile('wb')

		self.timeout_event = threading.Event()
		self.timeout_handle = None

	def extend_session(self, amt=1):
		self.MAX_REQUESTS += amt

	def collect_headers(self):
		hbuf_len = 0

		# todo: It'd be ideal to get rid of this try-except
		try:
			line = self.rfile.readline(self.MAX_HEADER_BUF_SIZE)
			if not line.endswith(b'\r\n'):
				raise StopExecution(
					'Cannot collect header data further, because '
					'the request is either invalid or too large.'
				)
			hbuf_len += len(line)
			hdata = [line.decode()]
		except Exception as e:
			self.timeout_event.clear()
			raise ConnectionAbortedError('Could not collect header data.')

		self.timeout_event.clear()

		if not hdata[0] or hdata[0] == '\r\n':
			return []

		while True:
			if hbuf_len >= self.MAX_HEADER_BUF_SIZE:
				raise StopExecution(
					'Cannot collect header data further, because '
					'the buffer size has exceeded allowed limits.'
				)

			line = self.rfile.readline(self.MAX_HEADER_BUF_SIZE)
			if not line or line == b'\r\n':
				break
			hbuf_len += len(line)
			hdata.append(line.decode().strip())

		return hdata

	# Close all the files and the socket connection
	def close(self):
		try:
			# self.wfile.flush()
			self.rfile.flush()

			# self.wfile.close()
			self.rfile.close()

			self.cl_con.shutdown(socket.SHUT_RDWR)
			self.cl_con.close()
		except Exception as e:
			pass

	def max_life_end(self):
		print(self.session_id, 'Max Life End Triggered')
		self.served_requests = self.MAX_REQUESTS
		self.timeout_event.wait()
		print('='*10,'Timeout event released')
		self.close()

	def run(self):
		# todo: this was added recently VVV
		self.timeout_handle = threading.Timer(self.MAX_LIFE, self.max_life_end)
		self.timeout_handle.start()
		# todo: this was added recently ^^^

		try:
			while self.served_requests <= self.MAX_REQUESTS:
				print(
					'Pool', self.pool_id,
					'Session', self.session_id,
					'Serving request #', self.served_requests,
					'of', self.MAX_REQUESTS
				)

				self.served_requests += 1

				self.timeout_event.set()
				header_data = self.collect_headers()

				if not header_data:
					raise ConnectionAbortedError('No header data present')

				if not self.timeout_event.is_set():
					self.callback(MinHTTPRequest(
						header_data,
						self,
						self.shared_data,
						self.served_requests == self.MAX_REQUESTS
					))

					# self.wfile.flush()
		except StopExecution as err:
			print(
				'Session', self.session_id,
				'Stopped executing, because', err
			)
		except ConnectionAbortedError as err:
			print('Session', self.session_id, 'Got connection aborted')
		except ConnectionResetError as err:
			print('Session', self.session_id, 'Got connection reset')
		except TimeoutError as err:
			print('Session', self.session_id, 'Got connection timed out')
		except BrokenPipeError as err:
			print('Session', self.session_id, 'Got connection pipe broken')
		except Exception as e:
			print_exception(e)
		finally:
			# todo: this was added recently vvv
			self.timeout_handle.cancel()
			# todo: this was added recently ^^^

			self.close()
			print(
				'Session', self.session_id, 'Finished'
			)


# hts = "http session"
def _http_session_pool(
	skt,
	release_event,
	callback,
	max_sessions,
	shared_data=None
):
	try:
		pool_id = random.random()

		print('Created session thread pool process')

		hts_pool = []

		while len(hts_pool) <= max_sessions:
			conn, address = skt.accept()
			print(pool_id, 'Accepted connection from', address)

			hts_thread = threading.Thread(
				target=HTTPSession(conn, callback, shared_data).run,
				daemon=True
			)
			hts_pool.append(hts_thread)

			hts_thread.start()

		release_event.set()
		print('HT Session pool served max sessions. Collapsing')

		for thread in hts_pool:
			thread.join()

		# release_event.set()

		print('Successfully collapsed all threads from the pool')

	except Exception as e:
		print_exception(e)
		raise e


def __http_session_pool(
	skt,
	release_event,
	callback,
	max_sessions,
	shared_data=None
):
	try:
		pool_id = random.random()

		print('Created session thread pool process')

		executor = ThreadPoolExecutor(max_workers=32)

		for _ in range(max_sessions):
			conn, addr = skt.accept()
			executor.submit(
				HTTPSession(conn, callback, shared_data).run
			)

		release_event.set()
		print('HT Session pool served max sessions. Collapsing')

		executor.shutdown(wait=True)

		print('Successfully collapsed all threads from the pool')
	except Exception as e:
		print_exception(e)
		raise e


def http_session_pool(
	skt_data,
	release_event,
	callback,
	max_sessions,
	shared_data=None
):
	try:
		if IS_WIN:
			skt = skt_data
		else:
			skt = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

			skt.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
			skt.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

			skt.bind(('', skt_data))

			skt.listen(4096)

		pool_id = random.random()

		print('Created session thread pool process')

		executor = ThreadPoolExecutor(max_workers=32)

		for _ in range(max_sessions):
			conn, addr = skt.accept()
			executor.submit(
				HTTPSession(conn, callback, shared_data, pool_id)
				.run
			)

		skt.close()

		release_event.set()
		print('HT Session pool served max sessions. Collapsing')

		executor.shutdown(wait=True)

		print('Successfully collapsed all threads from the pool')
	except Exception as e:
		print_exception(e)
		raise e


def http_worker_unit(
	skt,
	callback,
	max_sessions,
	shared_data=None
):
	try:
		print('Creating parent HTTP worker unit process')

		proc_stack = []

		while True:
			if len(proc_stack) >= MinHTTP.MAX_HTTP_WORKER_STACK:
				time.sleep(1)

				# todo: duplicate debug code
				for idx, proc in enumerate(proc_stack):
					if not proc.is_alive():
						proc.join()
						# del proc_stack[idx]
						proc_stack[idx] = None

				while None in proc_stack:
					del proc_stack[
						proc_stack.index(None)
					]

				continue

			release_event = multiprocessing.Event()
			proc = multiprocessing.Process(
				target=http_session_pool,
				args=(
					skt,
					release_event,
					callback,
					max_sessions,
					shared_data,
				)
			)
			proc_stack.append(proc)
			proc.start()

			# Wait for the process to signal that it
			# will no longer do new work and is only
			# finishing the remaining stuff
			release_event.wait()

			# todo: this is debug
			_len_before = len(proc_stack)

			# todo: rename the var
			for idx, proc in enumerate(proc_stack):
				if not proc.is_alive():
					proc.join()
					# del proc_stack[idx]
					proc_stack[idx] = None

			while None in proc_stack:
				del proc_stack[
					proc_stack.index(None)
				]

			print(
				'The current stack process gave up. Cleanup performed.'
				'Stack before:', _len_before, 'After:', len(proc_stack)
			)

	except Exception as e:
		print_exception(e)



class MinHTTP:
	# The amount of persistent workers
	WORKER_POOL_SIZE = 4

	# How many HTTP sessions can go through a worker
	# before it resets and frees up memory
	MAX_SESSIONS = 15

	# Used for skt.listen(self.CON_HARD_LIMIT)
	CON_HARD_LIMIT = 4096

	# Timeout between cycles of checking whether
	# all the processess in hts worker pool are alive
	HTS_STATUS_CHECK_COOLDOWN = 30

	MAX_HTTP_WORKER_STACK = 3

	def __init__(
		self,
		callback,
		shared_data=None,
		tgt_skt=None,
		tgt_port=None
	):
		self.callback = callback

		self.shared_data = shared_data

		self.tgt_skt = tgt_skt
		self.tgt_port = tgt_port

		# The socket object this class is using
		self.skt = None
		# Required if specified port is 0
		self.addr_info = None

	def _run(self):
		if not self.tgt_skt:
			skt = socket.socket()
			skt.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
			skt.bind(
				('', self.tgt_port)
			)
			skt.listen(self.CON_HARD_LIMIT)
		else:
			# Todo: make sure it's listening
			skt = self.tgt_skt

		self.skt = skt

		# skt.settimeout(RECV_TIMEOUT)

		self.addr_info = skt.getsockname()

		srv_worker_pool = [None]*self.WORKER_POOL_SIZE

		# Apparently, hts workers can crash irreversibly
		while True:
			try:
				for idx, proc in enumerate(srv_worker_pool):
					if not proc or not proc.is_alive():
						if proc != None:
							print('Found dead HTS worker')
							proc.join()

						srv_worker = multiprocessing.Process(
							target=http_worker_unit,
							args=(
								skt,
								self.callback,
								self.MAX_SESSIONS,
								self.shared_data,
							)
						)
						srv_worker_pool[idx] = srv_worker
						srv_worker.start()

				time.sleep(self.HTS_STATUS_CHECK_COOLDOWN)
			except Exception as e:
				print_exception(e)
				continue

	def run(self):
		if not self.tgt_skt:
			skt = socket.socket()
			skt.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

			if not IS_WIN:
				skt.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

			skt.bind(
				('', self.tgt_port)
			)
			skt.listen(self.CON_HARD_LIMIT)
		else:
			# Todo: make sure it's listening
			skt = self.tgt_skt

		self.skt = skt

		# skt.settimeout(RECV_TIMEOUT)

		self.addr_info = skt.getsockname()

		try:
			if not self.tgt_skt and not IS_WIN:
				skt.close()
		except Exception as e:
			pass

		srv_worker_pool = [None]*self.WORKER_POOL_SIZE

		# Apparently, hts workers can crash irreversibly
		while True:
			try:
				for idx, proc in enumerate(srv_worker_pool):
					if not proc or not proc.is_alive():
						if proc != None:
							print('Found dead HTS worker')
							proc.join()

						srv_worker = multiprocessing.Process(
							target=http_worker_unit,
							args=(
								self.addr_info[1] if (not IS_WIN) else skt,
								self.callback,
								self.MAX_SESSIONS,
								self.shared_data,
							)
						)
						srv_worker_pool[idx] = srv_worker
						srv_worker.start()

				time.sleep(self.HTS_STATUS_CHECK_COOLDOWN)
			except Exception as e:
				print_exception(e)
				continue



class Endpoint:
	def __init__(
		self,
		parent,
		handler=None,
		wildcard=False,
		name=None,
		terminal=False
	):
		self.handler = None
		self.children = {}
		self.parent = parent
		self.name = name

		self.wildcard = wildcard
		self.terminal = terminal


class Router:
	"""
		Route syntax:
		- /a/b
		Match /a/b precisely.
		/a/g = denied

		- /a/*/b:
		Match "a" -> "match anything" -> match "b", such as
		/a/pootis/b
		/a/fuck/b

		- /a*>:
		Match anything after /a, such as
		/a/fuck/shit/pootis/ded/nen
		/a/shit/sadwich/sssssss
	"""
	def __init__(self, routes):
		self.handle_404 = None
		self.route_tree = Endpoint(None, None, name='/')

		for route_handler in routes:
			print()

			if getattr(route_handler, 'handle_404', False):
				self.handle_404 = route_handler
				continue

			route = route_handler.route.strip()
			if route == '/':
				self.route_tree.handler = route_handler
				print('Created "/" route:', route_handler)
				continue

			route_stack = route.strip(' /').split('/')
			route_stack.reverse()
			print('Route stack:', route_stack)

			current_parent = self.route_tree
			endp_data = None

			while route_stack:
				endp_name = route_stack.pop().strip()
				endp_name_normalized = endp_name.replace('*>', '')
				print(
					'Processing endpoint', endp_name.ljust(10, ' '),
					'\n',
					'Whose parent is:',    current_parent.name
				)
				endp_data = self.route_tree.children.get(endp_name_normalized)

				if not endp_data:
					endp_data = Endpoint(
						current_parent,
						wildcard=(endp_name == '*'),
						name=endp_name_normalized,
						terminal=endp_name.endswith('*>')
					)
					current_parent.children[endp_name_normalized] = endp_data

				current_parent = endp_data

			# Once done traversing to the target path - actually
			# assign the handler class
			endp_data.handler = route_handler

	def nav(self, htrequest):
		# print()
		# print('Navigating', htrequest.path)
		handler = None
		path_stack = htrequest.path.strip(' /')
		if not path_stack:
			handler = self.route_tree.handler
		else:
			path_stack = path_stack.split('/')
			path_stack.reverse()

		if not handler:
			endpoint_data = self.route_tree

			while path_stack:
				endpoint_name = path_stack.pop()
				# print(
				# 	'Searching',
				# 	endpoint_data.name.ljust(10, ' '),
				# 	'for', endpoint_name, '\n',
				# 	'\n'.join(
				# 		[f'{k} {v}' for k,v in endpoint_data.children.items()]
				# 	),
				# 	'\n',
				# 	path_stack
				# )

				if endpoint_data.children:
					first_key, first_route = tuple(
						endpoint_data.children.items()
					)[0]

				# print('.items()', first_key, first_route)

				if first_key == '*':
					print('Got wildcard trigger')
					endpoint_data = first_route
					handler = first_route.handler
					continue
				else:
					endpoint_data = endpoint_data.children.get(endpoint_name)

				if endpoint_data and endpoint_data.terminal:
					handler = endpoint_data.handler
					htrequest.adjusted_path = (
						htrequest.path
						.strip('/')
						.split(endpoint_data.name)[-1]
						.strip(' /')
					)
					break

				if not endpoint_data:
					# print('Invalid shit:', htrequest.path)
					if self.handle_404:
						self.handle_404(htrequest).run(htrequest)
					else:
						htrequest.deny(404)
					return

				handler = endpoint_data.handler

		if not handler:
			# print('Invalid shit:', htrequest.path)
			if self.handle_404:
				self.handle_404(htrequest).run(htrequest)
			else:
				htrequest.deny(404)
			return

		handler(htrequest).run(htrequest)


def test_routing():
	class _htrequest:
		def __init__(self, test_path):
			self.path = test_path
			self.adjusted_path = None

		def deny(self):
			print('Denied', self.path)
			pass

	class _test_routes:
		def __init__(self, htrequest):
			# print('Init route handler', htrequest.path, self.route)
			pass
		def run(self, htrequest):
			print('Running route', htrequest.path, self.route, htrequest.adjusted_path)
			pass

	class _test_route_a(_test_routes):
		route = '/'

	class _test_route_b(_test_routes):
		route = '/home'

	class _test_route_c(_test_routes):
		route = '/sex/boobs/*'

	class _test_route_d(_test_routes):
		route = '/fuck/*/shit'

	class _test_route_e(_test_routes):
		route = '/home/ded'

	class _test_route_f(_test_routes):
		route = '/fuck/*/shit/*/ded/*'

	class _test_route_g(_test_routes):
		route = '/home/pootis'

	class _test_route_h(_test_routes):
		route = '/kleiner/shounic*>'

	class _test_route_i(_test_routes):
		route = '/shounic*>'
			

	_router = Router([
		_test_route_a,
		_test_route_b,
		_test_route_c,
		_test_route_d,
		_test_route_e,
		_test_route_f,
		_test_route_g,
		_test_route_h,
		_test_route_i,
	])

	# /home
	# /sex/boobs/*
	# /fuck/*/shit
	# /home/ded
	# /fuck/*/shit/*/ded/*
	# /home/pootis
	# /*

	_tests = [
		'/',
		'/home',
		'/home/pootis',
		'/_fuck',
		'/home/_kys',
		'/sex',
		'/boobs',
		'/sex/boobs',
		'/fuck/_kys/shit',
		'/fuck/_kys/shit/_kys2/ded/_kys3',
		'/kleiner/shounic/maps/barnb/data.zip',
		'/shounic/maps/barnb/data.zip',
	]

	for t in _tests:
		print()
		_router.nav(
			_htrequest(t)
		)

	# import timeit
	# print(timeit.timeit(
	# 	lambda: _router.nav(_htrequest(_tests[8])),
	# 	number=500_000
	# ))



if __name__ == '__main__':
	test_routing()





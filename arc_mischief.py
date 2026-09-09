from __future__ import annotations

import io
import os
import tarfile
import zipfile
import html

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Optional
from xml.etree import ElementTree as ET

try:
	import py7zr
except ImportError:
	py7zr = None



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



def sizeof_fmt(num, suffix='B'):
	for unit in SIZE_UNITS_PPRINT:
		if abs(num) < 1024.0:
			# return f'{num:3.1f}{unit}{suffix}'
			return (f'{num:3.1f}', f'{unit}{suffix}')
		num /= 1024.0
	return (f'{num:.1f}', 'Yi')




class ArchiveNode:
	def __init__(self, name='', parent=None):
		self.name = name
		self.parent = parent
		self.children = {}
		self.size = None

	def get_path(self):
		parts = []
		node = self

		while node and node.name:
			parts.append(node.name)
			node = node.parent

		return '/'.join(reversed(parts))



class ArchiveTomfoolery:
	ARCHIVE_TYPE_MAP = (
		('tar', (
			'.tar',
			'.tar.gz',
			'.tar.bz2',
			'.tar.bz2',
			'.tar.xz',
			'.tgz',
			'.tbz2',
			'.txz',
		)),

		('zip', (
			'.zip',
		)),

		('7z', (
			'.7z',
		)),
	)

	def __init__(self, fpath):
		self.fpath = Path(fpath)

		self._archive_type = None
		self._html_string = None

	@property
	def archive_type(self):
		if self._archive_type != None:
			return self._archive_type

		for archive_type, fext_array in self.ARCHIVE_TYPE_MAP:
			for fext in fext_array:
				if self.fpath.name.endswith(fext):
					self._archive_type = archive_type
					return self._archive_type

		self._archive_type = False
		return self._archive_type

	@property
	def buf(self):
		if self.archive_type == 'zip':
			return zipfile.ZipFile(self.fpath, 'r')
		if self.archive_type == 'tar':
			return tarfile.open(self.fpath, 'r:*')
		if self.archive_type == '7z':
			return py7zr.SevenZipFile(self.fpath, "r")



# Recursive, because when was the last time you saw an archive
# THOUSANDS of directories deep ?
class ArchiveHTML(ArchiveTomfoolery):
	def __init__(self, fpath):
		super().__init__(fpath)
		self.root_node = ArchiveNode()

	def construct_node_html(self, name, node):
		if node.children:
			children_html = ''.join(
				self.construct_node_html(child_name, child_node)
				for child_name, child_node in sorted(
					node.children.items(),
					key=lambda item: (bool(item[1].children), item[0].lower()),
				)
			)

			return (
				f'<details open class="tree_item">'
				f'<summary class="tree_item">'
				f'<img src="/cdn/static/assets/dir_icon.svg">'
				f'<div class="item_name">{html.escape(name)}</div>'
				f'<div></div>'
				f'</summary>'
				f'{children_html}'
				f'</details>'
			)

		size_text = '' if (node.size is None) else ' '.join(sizeof_fmt(node.size))
		return (
			f'<div class="tree_item tfile">'
			f'<img src="/cdn/static/assets/file_icon.svg">'
			# f'<div class="item_name">{html.escape(name)}</div>'
			f'<a abspath="{node.get_path()}" class="item_name">{html.escape(name)}</a>'
			f'<div class="item_size">{html.escape(size_text)}</div>'
			f'</div>'
		)

	def construct_tree_html(self):
		return ''.join(
			self.construct_node_html(name, node)
			for name, node in sorted(
				self.root_node.children.items(),
				key=lambda item: (bool(item[1].children), item[0].lower()),
			)
		)

	def insert_path(self, member_name, size):
		member_name = member_name.replace('\\', '/').strip('/')
		if not member_name:
			return

		parts = PurePosixPath(member_name).parts
		node = self.root_node

		for part in parts[:-1]:
			if part not in node.children:
				node.children[part] = ArchiveNode(part, node)
			node = node.children[part]

		leaf = parts[-1]
		if leaf not in node.children:
			node.children[leaf] = ArchiveNode(leaf, node)

		node.children[leaf].size = size	

	def iter_members(self, path):
		with self.buf as archive_data:
			if self.archive_type == 'zip':
				for info in archive_data.infolist():
					if info.is_dir():
						continue
					yield info.filename, info.file_size

			if self.archive_type == 'tar':
				for member in archive_data.getmembers():
					if member.isdir():
						continue
					if not member.isfile():
						continue
					yield member.name, member.size


			if self.archive_type == 'tar':
				for info in archive_data.list():
					if info.is_directory:
						continue
					yield info.filename, info.uncompressed

	@property
	def contents_html(self):
		if self._html_string != None:
			return self._html_string

		for member_name, size in self.iter_members(self.fpath):
			self.insert_path(member_name, size)

		self._html_string = self.construct_tree_html()
		return self._html_string

	def __str__(self):
		return self.contents_html





class SevenZipWriter:
	def __init__(self, chunk_callback):
		self.total = 0
		self.chunk_callback = chunk_callback

	def write(self, data):
		self.chunk_callback(data)
		self.total += len(data)
		return len(data)

	def read(self, size=None):
		# Some random stupid shit required by py7zr
		return b''

	def seek(self, offset, whence=0):
		# Trash
		return 0


class SevenZipFactory:
	def __init__(self, chunk_callback):
		self.obj = None
		self.chunk_callback = chunk_callback

	def create(self, filename):
		self.obj = SevenZipWriter(chunk_callback)
		return self.obj


class ArchiveExtractor(ArchiveTomfoolery):
	CHUNK_SIZE = 1024**2


	def extract(self, member_path, callback):
		with self.buf as archive_data:
			if self.archive_type == 'zip':
				with archive_data.open(member_path, 'r') as fbuf:
					while (chunk := fbuf.read(self.CHUNK_SIZE)):
						callback(chunk)

			if self.archive_type == 'tar':
				member_data = archive_data.getmember(member_path)
				with archive_data.extractfile(member_data) as fbuf:
					while (chunk := fbuf.read(chunk_size)):
						chunk_callback(chunk)

			if self.archive_type == '7z':
				archive_data.extract(
					targets=[member_path],
					factory=SevenZipFactory(callback)
				)






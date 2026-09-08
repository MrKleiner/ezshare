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

import py7zr



# FOLDER_ICON = """data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"><path fill="%23d7b56d" d="M1.5 4A1.5 1.5 0 0 1 3 2.5h3.5c.4 0 .8.16 1.1.44L8.5 4h4A1.5 1.5 0 0 1 14 5.5v6A1.5 1.5 0 0 1 12.5 13h-9A1.5 1.5 0 0 1 2 11.5z"/></svg>"""
# FILE_ICON = """data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"><path fill="%23aab2c0" d="M4 1.5A1.5 1.5 0 0 1 5.5 0h3.3c.4 0 .8.16 1.1.44l2.7 2.7c.28.28.44.67.44 1.06v10.3A1.5 1.5 0 0 1 11.5 16h-6A1.5 1.5 0 0 1 4 14.5z"/></svg>"""


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




class Node:
	def __init__(self, name="", parent=None):
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

		return "/".join(reversed(parts))


def _normalize_member_name(name: str) -> str:
	name = name.replace("\\", "/").strip("/")
	return name


def _insert_path(root: Node, member_name: str, size):
	member_name = member_name.replace("\\", "/").strip("/")
	if not member_name:
		return

	parts = PurePosixPath(member_name).parts
	node = root

	for part in parts[:-1]:
		if part not in node.children:
			node.children[part] = Node(part, node)
		node = node.children[part]

	leaf = parts[-1]
	if leaf not in node.children:
		node.children[leaf] = Node(leaf, node)

	node.children[leaf].size = size


def _archive_kind(path: str | os.PathLike[str]) -> str:
	p = Path(path)
	name = p.name.lower()
	suffixes = [s.lower() for s in p.suffixes]

	if name.endswith(".tar.gz") or name.endswith(".tar.bz2") or name.endswith(".tar.xz") or name.endswith(".tgz") or name.endswith(".tbz2") or name.endswith(".txz"):
		return "tar"

	if ".zip" in suffixes or name.endswith(".zip"):
		return "zip"

	if ".7z" in suffixes or name.endswith(".7z"):
		return "7z"

	if ".tar" in suffixes or name.endswith(".tar"):
		return "tar"

	raise ValueError(f"Unsupported archive type: {path}")


def _iter_members(path: str | os.PathLike[str]):
	kind = _archive_kind(path)

	if kind == "zip":
		with zipfile.ZipFile(path, "r") as z:
			for info in z.infolist():
				if info.is_dir():
					continue
				yield info.filename, info.file_size

	elif kind == "tar":
		with tarfile.open(path, "r:*") as t:
			for member in t.getmembers():
				if member.isdir():
					continue
				if not member.isfile():
					continue
				yield member.name, member.size

	elif kind == "7z":
		with py7zr.SevenZipFile(path, "r") as z:
			for info in z.list():
				if info.is_directory:
					continue
				yield info.filename, info.uncompressed

	else:
		raise ValueError(f"Unsupported archive type: {path}")


def archive_tree(path: str | os.PathLike[str]) -> Node:
	root = Node()
	for name, size in _iter_members(path):
		_insert_path(root, name, size)
	return root


def render_text_tree(root: Node) -> str:
	lines: list[str] = []

	def walk(node: Node, prefix: str = "") -> None:
		items = sorted(
			node.children.items(),
			key=lambda item: (item[1].children != {}, item[0].lower()),
		)

		for index, (name, child) in enumerate(items):
			last = index == len(items) - 1
			branch = "└── " if last else "├── "
			next_prefix = prefix + ("    " if last else "│   ")

			if child.children:
				lines.append(f"{prefix}{branch}{name}/")
				walk(child, next_prefix)
			else:
				size = child.size if child.size is not None else 0
				lines.append(f"{prefix}{branch}{name} ({size} bytes)")

	walk(root)
	return "\n".join(lines)


def render_xml_tree(root: Node, archive_name: str = "archive") -> str:
	def build_xml(parent: ET.Element, node: Node) -> None:
		for name, child in sorted(
			node.children.items(),
			key=lambda item: (item[1].children != {}, item[0].lower()),
		):
			if child.children:
				elem = ET.SubElement(parent, "adir", name=name)
				build_xml(elem, child)
			else:
				attrs = {"name": name, "size": str(child.size if child.size is not None else 0)}
				ET.SubElement(parent, "afile", attrs)

	archive = ET.Element("archive", name=archive_name)
	build_xml(archive, root)
	return ET.tostring(archive, encoding="unicode", short_empty_elements=False)


def list_archive_text(path: str | os.PathLike[str]) -> str:
	root = archive_tree(path)
	return render_text_tree(root)


def list_archive_xml(path: str | os.PathLike[str]) -> str:
	root = archive_tree(path)
	return render_xml_tree(root, archive_name=Path(path).name)


def _copy_stream(src: BinaryIO, dst: BinaryIO, chunk_size: int = 1024 * 1024) -> int:
	total = 0
	while True:
		chunk = src.read(chunk_size)
		if not chunk:
			break
		dst.write(chunk)
		total += len(chunk)
	return total


def build_html_tree_fragment(root: Node) -> str:
	def render_node(name: str, node: Node) -> str:
		if node.children:
			children_html = "".join(
				render_node(child_name, child_node)
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

		size_text = "" if node.size is None else ' '.join(sizeof_fmt(node.size))
		return (
			f'<div class="tree_item tfile">'
			f'<img src="/cdn/static/assets/file_icon.svg">'
			# f'<div class="item_name">{html.escape(name)}</div>'
			f'<a abspath="{node.get_path()}" class="item_name">{html.escape(name)}</a>'
			f'<div class="item_size">{html.escape(size_text)}</div>'
			f'</div>'
		)

	return "".join(
		render_node(name, node)
		for name, node in sorted(
			root.children.items(),
			key=lambda item: (bool(item[1].children), item[0].lower()),
		)
	)


def archive_to_html_fragment(archive_path, build_root_fn, insert_path_fn, render_fragment_fn):
	root = build_root_fn()

	for member_name, size in _iter_members(archive_path):
		insert_path_fn(root, member_name, size)

	return render_fragment_fn(root)


def view_archive_html(fpath):
	return archive_to_html_fragment(
		fpath,
		build_root_fn=lambda: Node(),
		insert_path_fn=_insert_path,
		render_fragment_fn=build_html_tree_fragment,
	)


class _7zStreamingWriter:
	def __init__(self, out_path: str | os.PathLike[str]):
		self._out_path = Path(out_path)
		self._fh = open(self._out_path, "wb")
		self._size = 0

	def write(self, data: bytes | bytearray) -> int:
		n = self._fh.write(data)
		self._size += n
		return n

	def read(self, size: Optional[int] = None) -> bytes:
		return b""

	def seek(self, offset: int, whence: int = 0) -> int:
		return self._fh.seek(offset, whence)

	def flush(self) -> None:
		self._fh.flush()

	def size(self) -> int:
		return self._size

	def close(self) -> None:
		self._fh.close()


class _7zWriterFactory:
	def __init__(self, out_path: str | os.PathLike[str]):
		self.out_path = out_path
		self.products: dict[str, _7zStreamingWriter] = {}

	def create(self, filename: str) -> _7zStreamingWriter:
		writer = _7zStreamingWriter(self.out_path)
		self.products[filename] = writer
		return writer


def read_from_archive(archive_path, member_path, chunk_callback, chunk_size=1024**2):
	archive_path = Path(archive_path)
	name = archive_path.name.lower()
	suffixes = [s.lower() for s in archive_path.suffixes]

	member_path = member_path.replace("\\", "/").strip("/")

	# --- ZIP ---
	if name.endswith(".zip") or ".zip" in suffixes:
		with zipfile.ZipFile(archive_path, "r") as z:
			with z.open(member_path, "r") as f:
				while True:
					chunk = f.read(chunk_size)
					if not chunk:
						break
					chunk_callback(chunk)
		return

	# --- TAR ---
	if (
		name.endswith((".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz"))
		or ".tar" in suffixes
	):
		with tarfile.open(archive_path, "r:*") as t:
			member = t.getmember(member_path)
			f = t.extractfile(member)

			if f is None:
				raise FileNotFoundError(f"Not a file: {member_path}")

			with f:
				while True:
					chunk = f.read(chunk_size)
					if not chunk:
						break
					chunk_callback(chunk)
		return

	# --- 7Z ---
	if name.endswith(".7z") or ".7z" in suffixes:
		class Writer:
			def __init__(self):
				self.total = 0

			def write(self, data):
				chunk_callback(data)
				self.total += len(data)
				return len(data)

			def read(self, size=None):
				return b""  # required by py7zr

			def seek(self, offset, whence=0):
				return 0  # not used

		class Factory:
			def __init__(self):
				self.obj = None

			def create(self, filename):
				self.obj = Writer()
				return self.obj

		factory = Factory()

		with py7zr.SevenZipFile(archive_path, "r") as z:
			z.extract(targets=[member_path], factory=factory)

		if factory.obj is None:
			raise FileNotFoundError(member_path)

		return

	raise ValueError(f"Unsupported archive: {archive_path}")















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
		return b""

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
	def __init__(self, fpath):
		super().__init__(fpath)

	def extract(self, member_path, callback):
		pass



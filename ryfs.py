#!/usr/bin/env python3
# ryfs.py
# manage RYFS disk images

import os
import sys
import struct
import argparse

version_info = (0, 5)
version = '.'.join(str(c) for c in version_info)

# create new RYFSv1 disk image
def ryfs_create():
    if not quiet:
        if use_boot_sector:
            print("creating bootable RYFSv1 image", "\"" + ryfs_image.name + "\"", "of size", ryfs_image_size, "bytes with label", "\"" + ryfs_image_label + "\"")
        else:
            print("creating RYFSv1 image", "\"" + ryfs_image.name + "\"", "of size", ryfs_image_size, "bytes with label", "\"" + ryfs_image_label + "\"")

    # fill new file with zeros
    ryfs_image.seek(0)
    ryfs_image.write(bytearray(ryfs_image_size))
    ryfs_image.seek(0)

    if use_boot_sector:
        ryfs_image.write(ryfs_image_boot.read(512))

    ryfs_image.seek(512)

    # write number of bitmap sectors, 1 byte
    ryfs_image_bitmap_sectors = int(round_ceil(ryfs_image_size_sectors, 4096)/4096)
    ryfs_image.write(struct.pack('<B', ryfs_image_bitmap_sectors))
    # write RYFSv1 version header
    ryfs_image.write(bytearray([1,ord('R'),ord('Y')]))
    # write size of image, 2 bytes, little endian
    ryfs_image.write(struct.pack('<H', ryfs_image_size_sectors))
    # write directory label
    ryfs_image.write(str_to_bytearray(ryfs_image_label))

    # seek to first bitmap sector
    ryfs_image.seek(1024)
    # mark first two sectors as used (boot sector and directory sector)
    bitmap = 0b0000000000000011
    # mark bitmap sectors as used
    for bitmap_sector in range(0, ryfs_image_bitmap_sectors):
        bitmap = bitmap | 1 << bitmap_sector + 2
    ryfs_image.write(struct.pack('<I', bitmap))

# add file to an existing RYFSv1 disk image
def ryfs_add():
    # if this file already exists, delete it first
    if ryfs_find_entry(extra_file_name, extra_file_ext) != None:
        print("replacing existing file")
        ryfs_remove()

    if not quiet:
        print("adding file", "\"" + extra_file_name + "." + extra_file_ext + "\"", "of size", extra_file_size, "bytes to RYFSv1 filesystem with label", "\"" + ryfs_image_label + "\"")

    # find first empty file entry
    first_free_entry = ryfs_find_free_entry()
    if first_free_entry == None:
        print("all file entries are used! failing")
        return
    ryfs_image.seek(first_free_entry)

    first_free_sector = ryfs_find_free_sector()
    if first_free_sector == None:
        print("all sectors are used! failing")
        return

    # write number of first file sector, 2 bytes, little endian
    ryfs_image.write(struct.pack('<H', first_free_sector))

    # write file size in sectors, 2 bytes, little endian
    ryfs_image.write(struct.pack('<H', extra_file_size_sectors))

    # write null-terminated 8.3 file name, 12 bytes
    spaces = ' ' * (8 - len(extra_file_name))
    ryfs_image.write(bytearray(extra_file_name, 'utf-8'))
    ryfs_image.write(bytearray(spaces, 'utf-8'))
    ryfs_image.write(bytearray(extra_file_ext, 'utf-8'))
    ryfs_image.write(bytearray('\x00', 'utf-8'))

    extra_file.seek(0)

    # write file data
    for sector in range(0, extra_file_size_sectors):
        # find a free sector to use as the current sector to write to
        next_free_sector = ryfs_find_free_sector()
        ryfs_image.seek(next_free_sector*512)
        ryfs_mark_used(next_free_sector)
        # find another free sector to use as the next sector of this file
        next_free_sector = ryfs_find_free_sector()
        ryfs_image.write(bytearray([255,0]))
        if sector != extra_file_size_sectors-1:
            # this is not the last sector of this file
            # write the nomber of the next file sector
            ryfs_image.write(struct.pack('<H', next_free_sector))
            # since this is not the last sector, we don't care about the size of it (we know it's 512 bytes)
            ryfs_image.write(struct.pack('<H', 0))
        else:
            # this is the last sector of this file
            # there is no next sector for this file
            ryfs_image.write(struct.pack('<H', 0))
            # write the size of the last sector
            ryfs_image.write(struct.pack('<H', extra_file_size - extra_file.tell()))
        # zero sector first to ensure there is no remaining data from previous files
        ryfs_image.write(bytearray(506))
        ryfs_image.seek(ryfs_image.tell()-506)
        # write file data
        ryfs_image.write(extra_file.read(506))

    extra_file.close()

# remove file from an existing RYFSv1 disk image
def ryfs_remove():
    if not quiet:
        print("removing file", "\"" + extra_file_name + "." + extra_file_ext + "\"", "from RYFSv1 filesystem with label", "\"" + ryfs_image_label + "\"")

    # find file entry
    file_entry = ryfs_find_entry(extra_file_name, extra_file_ext)
    if file_entry == None:
        print("file not found! failing")
        return
    ryfs_image.seek(file_entry)

    ryfs_mark_free(int.from_bytes(ryfs_image.read(2), byteorder='little'))
    extra_file_size_sectors = int.from_bytes(ryfs_image.read(2), byteorder='little')
    ryfs_image.seek(ryfs_image.tell()-4)
    next_sector = int.from_bytes(ryfs_image.read(2), byteorder='little')

    # mark sectors as free
    for sector in range(0, extra_file_size_sectors):
        ryfs_image.seek(next_sector*512)
        ryfs_mark_free(next_sector)
        ryfs_image.seek(ryfs_image.tell()+2)
        next_sector = int.from_bytes(ryfs_image.read(2), byteorder='little')

    # remove file entry
    ryfs_image.seek(file_entry)
    ryfs_image.write(bytearray(16))

# export file from an existing RYFSv1 disk image
def ryfs_export():
    if not quiet:
        print("exporting file", "\"" + extra_file_name + "." + extra_file_ext + "\"", "from RYFSv1 filesystem with label", "\"" + ryfs_image_label + "\"")

    # find file entry
    file_entry = ryfs_find_entry(extra_file_name, extra_file_ext)
    if file_entry == None:
        print("file not found! failing")
        return
    ryfs_image.seek(file_entry)

    first_sector = int.from_bytes(ryfs_image.read(2), byteorder='little')
    size = int.from_bytes(ryfs_image.read(2), byteorder='little')

    # write file data
    ryfs_image.seek(first_sector*512)
    extra_file.seek(0)
    for sector in range(0, size):
        ryfs_image.seek(ryfs_image.tell()+2)
        next_sector = int.from_bytes(ryfs_image.read(2), byteorder='little')
        sector_size = int.from_bytes(ryfs_image.read(2), byteorder='little')
        if sector != size-1:
            # this is not the last sector of this file
            # read a whole sector's worth of data
            extra_file.write(ryfs_image.read(506))
        else:
            # this is the last sector of this file
            # only read the amount of data in this sector
            extra_file.write(ryfs_image.read(sector_size))
        ryfs_image.seek(next_sector*512)

    extra_file.close()

# list files in an existing RYFSv1 disk image
def ryfs_list():
    if not quiet:
        print("listing files from RYFSv1 filesystem with label", "\"" + ryfs_image_label + "\"")

    # seek to first file entry
    ryfs_image.seek(512+16)
    # print existing file entries
    for i in range(0,30):
        if ryfs_image.read(2) != b'\x00\x00':
            ryfs_image.seek(ryfs_image.tell()+2)
            entry = bytes(ryfs_image.read(12)).decode("utf-8")
            print(entry)
            continue
        ryfs_image.seek(ryfs_image.tell()+4)

# find first free sector
# returns None if all sectors are used
def ryfs_find_free_sector():
    # save current file pointer
    old_location = ryfs_image.tell()
    # seek to first bitmap sector
    ryfs_image.seek(1024)
    # find first free sector
    for bitmap_sector in range(0, ryfs_image_bitmap_sectors):
        for bitmap_byte in range(0, 512):
            first_clear_bit = find_first_clear(int.from_bytes(ryfs_image.read(1), byteorder='little'))
            if first_clear_bit != None:
                first_free_sector = (bitmap_sector*4096) + (bitmap_byte*8) + first_clear_bit
                ryfs_image.seek(old_location)
                return first_free_sector
    # no free sectors were found, return None
    ryfs_image.seek(old_location)
    return None

# find first free file entry
# returns None if all entries are used
def ryfs_find_free_entry():
    # save current file pointer
    old_location = ryfs_image.tell()
    # seek to first file entry
    ryfs_image.seek(512+16)
    # loop through each entry until we find an empty one
    for i in range(0,30):
        if ryfs_image.read(2) == b'\x00\x00':
            first_free_entry = (ryfs_image.tell()-2)
            ryfs_image.seek(old_location)
            return first_free_entry
        ryfs_image.seek(ryfs_image.tell()+14)
    # no free entries were found, return None
    ryfs_image.seek(old_location)
    return None

# find specified file entry
# returns None if entry doesn't exist
def ryfs_find_entry(name, ext):
    # save current file pointer
    old_location = ryfs_image.tell()

    spaces = ' ' * (8 - len(name))
    entry = bytearray()
    entry.extend(bytes(name, 'utf-8'))
    entry.extend(bytes(spaces, 'utf-8'))
    entry.extend(bytes(ext, 'utf-8'))
    entry.extend(bytes('\x00', 'utf-8'))
    ryfs_image.seek(512+20)
    for i in range(0,30):
        if bytearray(ryfs_image.read(12)) == entry:
            entry_location = (ryfs_image.tell()-16)
            ryfs_image.seek(old_location)
            return entry_location
        ryfs_image.seek(ryfs_image.tell()+4)
    # speficied file entry wasn't found, return None
    ryfs_image.seek(old_location)
    return None

# mark a sector as used
def ryfs_mark_used(sector):
    # save current file pointer
    old_location = ryfs_image.tell()

    bitmap_sector = int(round_ceil(sector+1, 4096)/4096)-1
    bitmap_byte = int(round_ceil(sector+1, 8)/8)-1
    if bitmap_byte >= 512:
        bitmap_byte = bitmap_byte % 512
    bitmap_bit = sector % 8

    final_location = 1024+(bitmap_sector*512)+bitmap_byte
    ryfs_image.seek(final_location)
    bitmap = int.from_bytes(ryfs_image.read(1), byteorder='little')
    bitmap = bitmap | 1 << bitmap_bit
    ryfs_image.seek(final_location)
    ryfs_image.write(struct.pack('<B', bitmap))

    # restore old file pointer
    ryfs_image.seek(old_location)

# mark a sector as free
def ryfs_mark_free(sector):
    # save current file pointer
    old_location = ryfs_image.tell()

    bitmap_sector = int(round_ceil(sector+1, 4096)/4096)-1
    bitmap_byte = int(round_ceil(sector+1, 8)/8)-1
    if bitmap_byte > 4096:
        bitmap_byte = bitmap_byte % 4096
    bitmap_bit = sector % 8

    final_location = 1024+(bitmap_sector*512)+bitmap_byte
    ryfs_image.seek(final_location)
    bitmap = int.from_bytes(ryfs_image.read(1), byteorder='little')
    bitmap = bitmap & ~(1 << bitmap_bit)
    ryfs_image.seek(final_location)
    ryfs_image.write(struct.pack('<B', bitmap))

    # restore old file pointer
    ryfs_image.seek(old_location)

def round_ceil(number, ceil_num):
    if number == 0:
        return ceil_num
    remainder = number % ceil_num
    if remainder == 0:
        return number
    return number + ceil_num - remainder

def find_first_clear(byte):
    if byte == 0b11111111:
        return None
    first_clear = 0
    while (byte % 2) == 1:
        first_clear += 1
        byte = byte >> 1
    return first_clear

def str_to_bytearray(text):
    array = bytearray()
    for char in text:
        array.append(ord(char))
    return array

def open_image(filename):
    if os.path.exists(filename):
        return open(filename, 'r+b')
    else:
        if ryfs_action == "create":
            return open(filename, 'w+b')
        else:
            print("error: file \"" + filename + "\" not found")
            sys.exit()

def open_file(filename):
    if ryfs_action == "export":
        return open(filename, 'w+b')
    if ryfs_action == "remove":
        return filename
    if os.path.exists(filename):
        return open(filename, 'r+b')
    else:
        print("error: file \"" + filename + "\" not found")
        sys.exit()

if __name__ == '__main__':
    arg_parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
        description="RYFS management tool - version " + version + "\nManage RYFSv1 disk images",
        epilog="example commands to create a 2KB image named \"ryfs.img\" with label \"Stuff\" and add \"hello.txt\" to it:\
            \n  create ryfs.img -l Stuff -s 2048\
            \n  add ryfs.img hello.txt\
            \n  list ryfs.img"
    )
    arg_parser.add_argument('action', nargs=1, help="\"add\", \"create\", \"export\", \"list\", \"remove\"")
    arg_parser.add_argument('image', nargs=1, help="disk image to manage")
    arg_parser.add_argument('file', nargs='?', help="file to modify (optional, depending on action)")
    arg_parser.add_argument('-b', '--boot-sector', dest="boot", type=argparse.FileType('rb'), help="use specified file as boot sector (must be 512 bytes)")
    arg_parser.add_argument('-l', '--label', type=str, default="RYFS", help="label of new directory (max. 8 characters, default \"RYFS\")")
    arg_parser.add_argument('-s', '--size', type=int, default=1474560, help="size in bytes of disk image to create (default 1474560 bytes)")
    arg_parser.add_argument('-q', '--quiet', action="store_true", help="disable all output except warnings and errors")
    args = arg_parser.parse_args()

    ryfs_action = args.action[0]
    ryfs_image = open_image(args.image[0])
    ryfs_image_size = args.size
    ryfs_image_size_sectors = int(round_ceil(ryfs_image_size, 512)/512)
    ryfs_image_label = args.label

    if ryfs_action == "add" or ryfs_action == "export" or ryfs_action == "remove":
        use_extra_file = True
    else:
        use_extra_file = False

    if use_extra_file:
        extra_file = open_file(args.file)
        if ryfs_action == "remove":
            extra_file_name, extra_file_ext = extra_file.split('.')
        else:
            extra_file_name, extra_file_ext = os.path.splitext(os.path.basename(extra_file.name))
            extra_file_size = os.fstat(extra_file.fileno()).st_size
            extra_file_size_sectors = int(round_ceil(extra_file_size, 506)/506)
            extra_file_ext = extra_file_ext[1:]

        if (len(extra_file_name) > 8) or (len(extra_file_ext) > 3):
            print("error: file name must be in 8.3 format")
            print(len(extra_file_name))
            print(len(extra_file_ext))
            sys.exit()

    quiet = args.quiet

    if args.boot != None:
        ryfs_image_boot = args.boot
        use_boot_sector = True
    else:
        use_boot_sector = False

    if ryfs_image_size > 16777216:
        print("error: RYFSv1 does not support read-write filesystems over 16MB")
        sys.exit()

    # if we aren't creating a new filesystem, get the existing label and numebr of bitmap sectors
    if ryfs_action != "create":
        ryfs_image.seek(512)
        ryfs_image_bitmap_sectors = int.from_bytes(ryfs_image.read(1), "little")
        ryfs_image.seek(512+6)
        ryfs_image_label = ryfs_image.read(8).decode("utf-8")

    if len(ryfs_image_label) > 8:
        print("error: filesystem label must be 8 characters or less")
        sys.exit()

    if ryfs_action == "add":
        ryfs_add()
    elif ryfs_action == "create":
        ryfs_create()
    elif ryfs_action == "export":
        ryfs_export()
    elif ryfs_action == "list":
        ryfs_list()
    elif ryfs_action == "remove":
        ryfs_remove()
    else:
        print("error: unknown action", "\"" + ryfs_action + "\"")
        sys.exit()

    ryfs_image.close()

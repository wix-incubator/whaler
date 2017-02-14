#!/usr/bin/env python
import os
import argparse
import ctypes
import string
import random
import stat
import traceback
import sys
import tempfile
import time


class Cleanup:
    def __init__(self):
        self.__directories = set()
        self.__mounts = set()
        self.__files = set()
        self.__remounts = {}

    def add_file(self, file_path):
        self.__files.add(file_path)

    def add_mount(self, mount_point):
        self.__mounts.add(mount_point)

    def add_directory(self, directory):
        self.__directories.add(directory)

    def add_remount(self, directory, writeable):
        self.__remounts[directory] = writeable

    def make_me_pretty(self):
        for mount in self.__mounts:
            try:
                SysAndStuff.unmount(mount)
            except:
                sys.stderr.write(traceback.format_exc())
        for file_path in self.__files:
            try:
                os.unlink(file_path)
            except:
                sys.stderr.write(traceback.format_exc())
        for directory in self.__directories:
            try:
                os.removedirs(directory)
            except:
                sys.stderr.write(traceback.format_exc())
        for remount in self.__remounts:
            try:
                SysAndStuff.remount_fs(remount, self.__remounts[remount])
            except:
                sys.stderr.write(traceback.format_exc())

        self.__init__()


class Utils:
    @staticmethod
    def id_generator(size=6, chars=string.ascii_uppercase + string.digits):
        return ''.join(random.choice(chars) for _ in range(size))


class SysAndStuff():
    __libc = ctypes.CDLL('libc.so.6', use_errno=True)
    CLONE_NEWNS = 0x00020000
    CLONE_NEWUTS = 0x04000000
    CLONE_NEWIPC = 0x08000000
    CLONE_NEWPID = 0x20000000
    CLONE_NEWNET = 0x40000000
    MS_REMOUNT = 32
    MS_RDONLY = 1
    MS_BIND = 4096

    @staticmethod
    def remount_fs(path, writeable):
        flags = 0
        if not writeable:
            flags = SysAndStuff.MS_RDONLY
        rc = SysAndStuff.__libc.mount("NONE", path, None, SysAndStuff.MS_REMOUNT | flags, None)
        if rc == -1:
            errno = ctypes.get_errno()
            raise IOError('Could\'nt remount %s as %s errno: %d (%s)' % (
                path, 'R/W' if writeable else 'RDONLY', errno, os.strerror(errno)))

    @staticmethod
    def enter_ns_of_pid(pid):
        namespaces = [{'ns': 'pid', 'setns': SysAndStuff.CLONE_NEWPID, 'fd': -1},
                      {'ns': 'net', 'setns': SysAndStuff.CLONE_NEWNET, 'fd': -1},
                      {'ns': 'uts', 'setns': SysAndStuff.CLONE_NEWUTS, 'fd': -1},
                      {'ns': 'ipc', 'setns': SysAndStuff.CLONE_NEWIPC, 'fd': -1},
                      {'ns': 'mnt', 'setns': SysAndStuff.CLONE_NEWNS, 'fd': -1}
                      ]
        try:
            for ns in namespaces:
                name = ns['ns']
                setns_opt = ns['setns']
                ns['fd'] = SysAndStuff.__libc.open('/proc/%d/ns/%s' % (pid, name), os.O_RDONLY)
                rc = SysAndStuff.__libc.setns(ns['fd'], setns_opt)
                if rc == -1:
                    errno = ctypes.get_errno()
                    raise IOError('Could\'nt enter %s namespace target pid: %d errno:%d (%s)' % (
                        name.upper(), pid, errno, os.strerror(errno)))
        except:
            sys.stderr.write(traceback.format_exc())
            return
        finally:
            for ns in namespaces:
                if ns['fd'] != -1:
                    SysAndStuff.__libc.close(ns['fd'])

    @staticmethod
    def create_dev_file(blk_dev):
        dev_name = '/dev/tmp_blk_dev_%s' % Utils.id_generator(5)
        os.mknod(dev_name, (0600 | stat.S_IFBLK), blk_dev)
        return dev_name

    @staticmethod
    def bind_mount(src, target):
        rc = SysAndStuff.__libc.mount(src, target, None, SysAndStuff.MS_BIND, None)
        if rc == -1:
            errno = ctypes.get_errno()
            raise IOError('Could\'nt bind mount %s  on %s errno:%d (%s)' % (
                src, target, errno, os.strerror(errno)))

    @staticmethod
    def mount_on_dev(src, target, fs_type='ext4'):
        rc = SysAndStuff.__libc.mount(src, target, fs_type, 0, None)
        if rc == -1:
            errno = ctypes.get_errno()
            raise IOError('Could\'nt mount %s (%s) on %s errno:%d (%s)' % (
                src, fs_type, target, errno, os.strerror(errno)))

    @staticmethod
    def unmount(target):
        rc = SysAndStuff.__libc.umount(target)
        if rc == -1:
            errno = ctypes.get_errno()
            raise IOError('Could\'nt unmount %s errno:%d (%s)' % (
                target, errno, os.strerror(errno)))

    @staticmethod
    def am_i_running_in_container():
        with open('/proc/self/cgroup', 'rb') as f:
            return 'docker' in f.read()

    @staticmethod
    def get_pid_of_container(pid=None, cid=None, alternative_sys_fs='/host/root/sys/fs'):
        sys_fs_root = '/sys/fs'
        if os.path.exists(alternative_sys_fs):
            sys_fs_root = alternative_sys_fs

        if pid:
            search_pid = int(pid)
            if os.path.exists('/proc/%d/cgroups' % search_pid):
                with open('/proc/%d/cgroups' % search_pid) as f:
                    data = f.read()
                    if 'docker' not in data:
                        raise ValueError('%d: PID isn\'t in a container' % search_pid)
            else:
                raise ValueError('%d: PID isn\'t in a container' % search_pid)
            return search_pid
        else:
            dirs = os.listdir('%s/cgroup/cpu/docker' % sys_fs_root)
            search_cid = None
            for d in dirs:
                if cid in d:
                    search_cid = d
                    break
            if not search_cid:
                raise ValueError('Container not found')
            with open('%s/cgroup/cpu/docker/%s/cgroup.procs' % (sys_fs_root, search_cid)) as f:
                data = f.readlines()
                search_pid = int(data[0].strip())
                return search_pid

    @staticmethod
    def ugly_overlay_hack(local_dir_path):
        """
        Ugly hack to overcome overlayFS when working on a container, will cause all files to be
        copied to the diff layer and so be available at the same mount point as the marker file
        :param local_dir_path:
        """
        for root, dirs, files in os.walk(local_dir_path, followlinks=False):
            try:
                for f in files:
                    os.utime(os.path.join(root, f), None)
            except:
                sys.stderr.write(traceback.format_exc())


class MountingLogic:
    def __init__(self, source_directory, destination_dir):
        if not os.path.isdir(source_directory):
            raise ValueError('%s: Not a directory' % source_directory)
        self.__source_directory = source_directory
        self.__marker_file = '.markerfile_%s' % Utils.id_generator(5)
        self.__marker_full_path = '%s/%s' % (self.__source_directory, self.__marker_file)
        self.__cleanup = Cleanup()
        self.__source_directory_relative_to_block_device_root = None
        self.__src_blk_device = None
        self.__destination_dir = destination_dir

    def __create_marker_file_get_blk_device(self):
        open(self.__marker_full_path, 'wb').close()
        self.__cleanup.add_file(self.__marker_full_path)
        return os.stat(self.__marker_full_path).st_dev

    def __locate_marker_on_mounted_fs(self, path):
        for root, dirs, files in os.walk(path, followlinks=False):
            for f in files:
                if self.__marker_file in f:
                    return root[len(path):]
        return None

    def __handle_ro_root_fs(self):
        if not os.access('/', os.W_OK):
            SysAndStuff.remount_fs('/', True)
            self.__cleanup.add_remount('/', False)

        if not os.access('/dev', os.W_OK):
            SysAndStuff.remount_fs('/dev', True)
            self.__cleanup.add_remount('/dev', False)

    def mount_source_directory_inside_the_container(self):
        try:
            # Handle read only root fs and /dev
            self.__handle_ro_root_fs()

            # Create directory in the container
            if not os.path.exists(self.__destination_dir):
                os.makedirs(self.__destination_dir)

            # Create a temporary mount point for the source directory block device
            temp_mount_dir = '/tmp_%s' % Utils.id_generator()
            os.makedirs(temp_mount_dir)
            self.__cleanup.add_directory(temp_mount_dir)

            block_device_of_source_directory = SysAndStuff.create_dev_file(self.__src_blk_device)
            self.__cleanup.add_file(block_device_of_source_directory)
            SysAndStuff.mount_on_dev(block_device_of_source_directory, temp_mount_dir)
            self.__cleanup.add_mount(temp_mount_dir)

            # Bind mount the source directory on the destination
            src_mount = '%s/%s' % (temp_mount_dir, self.__source_directory_relative_to_block_device_root)
            SysAndStuff.bind_mount(src_mount, self.__destination_dir)

        except:
            sys.stderr.write(traceback.format_exc())
        finally:
            self.__cleanup.make_me_pretty()

    def unmount_and_delete_leftovers_when_we_are_out(self):
        try:
            SysAndStuff.unmount(self.__destination_dir)
            if not os.access('/', os.W_OK):
                SysAndStuff.remount_fs('/', True)
                self.__cleanup.add_remount('/', False)
            os.removedirs(self.__destination_dir)
        except:
            sys.stderr.write(traceback.format_exc())
        finally:
            self.__cleanup.make_me_pretty()

    def figure_out_the_source_path_relative_to_block_device_root(self):
        """
        Since we might be in a container we use a "trick" here.
        We put a marker file in the source directory.
        Mount its block device and search for it after the mount
        :return:
        """
        if self.__source_directory_relative_to_block_device_root:
            return self.__source_directory_relative_to_block_device_root
        try:
            self.__src_blk_device = self.__create_marker_file_get_blk_device()
            marker_blk_device_path = SysAndStuff.create_dev_file(self.__src_blk_device)
            self.__cleanup.add_file(marker_blk_device_path)

            temp_mount_dir = tempfile.mkdtemp()
            self.__cleanup.add_directory(temp_mount_dir)
            SysAndStuff.mount_on_dev(marker_blk_device_path, temp_mount_dir)
            self.__cleanup.add_mount(temp_mount_dir)

            self.__source_directory_relative_to_block_device_root = self.__locate_marker_on_mounted_fs(temp_mount_dir)
            return self.__source_directory_relative_to_block_device_root
        except:
            sys.stderr.write(traceback.format_exc())
        finally:
            self.__cleanup.make_me_pretty()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-l', '--local-dir', help='local host directory', required=True)
    parser.add_argument('-d', '--destination-dir', help='container directory', default='/devtools')
    parser.add_argument('--cmd', help='cmd to run in container', default="busybox sh")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('-p', '--pid', help='enter container of PID')
    group.add_argument('-c', '--cid', help='enter container')

    args = parser.parse_args()

    try:
        pid = SysAndStuff.get_pid_of_container(args.pid, args.cid)
    except ValueError as ve:
        sys.stderr.write(ve.message + '\n')
        sys.exit(-1)

    mounting_logic = MountingLogic(args.local_dir, args.destination_dir)

    mounting_logic. \
        figure_out_the_source_path_relative_to_block_device_root()

    if SysAndStuff.am_i_running_in_container():
        SysAndStuff.ugly_overlay_hack(args.local_dir)

    SysAndStuff.enter_ns_of_pid(pid)

    mounting_logic. \
        mount_source_directory_inside_the_container()

    pid = os.fork()
    if pid == 0:
        try:
            os.chdir(args.destination_dir)
            args = args.cmd.split(' ')
            os.execv(args[0], args[1:])
        except:
            sys.stderr.write(traceback.format_exc())
        return
    else:
        print pid
        os.wait()
        mounting_logic.unmount_and_delete_leftovers_when_we_are_out()


if __name__ == "__main__":
    main()

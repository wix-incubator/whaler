Whaler
======

####What is it for?
We created whaler to allow us to add a volume to a running docker container.  
The use case was having a very slim container with no binaries, other then *one statically linked binary* we were using.  
We needed a way to go into the container for debugging purposes while it was running.
**Whaler** was the answer

####What does it do?
Whaler can either work on the native OS or inside a priveleged container with access to the raw block devices.  
It can then take a local directory, in the host or the priveleged container,  mount it inside a runing container, making it accesable there. So, one can then exec a shell inside that, container.

####How does it do it?
First we look for for a process within the running container, either by container id or directly PID.  
Then we locate where the directory we want to mount is relative to its block device. **Since we might be inside** a privileged container and do not know the block device, we use a temporary marker file, mount its block device and look for it relative to the device root.  
In our case since we run Whaler in a container and we used OverlayFS we needed to make sure all utilities we want to mount on the destination container are there so we employed an ugly hack to *touch* all files to bring them to the top layer, same as our marker file.  
Now comes the fun part:  

1.  Whaler enters the namespaces of the process within the destination container  
1.  Since we used a read-only root on our containers it remounts it r/w
3. Creates a temporary block device, with major and minor of the marker file
4. Mounts the temporary block device on a termporary directory 
5. Bind mounts the destination folder to the source folder
6. Runs a shell (must be on the source directory)
7. When you exit the shell, Whaler cleans up everything it left from the container

#### Limitations
* Does not work with filesystems that are not on block devices (/dev/**some disk**) 
* When runing from within a container it was only tested with OverlayFS am not sure if it will be able to locate the directory with AUFS / device mapper

### License

Copyright (c) 2017 Wix.com Ltd. All Rights Reserved. Use of this source code is governed by The Enhanced MIT License (EMIT) license that can be found in the [LICENSE](./LICENSE) file in the root of the source tree.
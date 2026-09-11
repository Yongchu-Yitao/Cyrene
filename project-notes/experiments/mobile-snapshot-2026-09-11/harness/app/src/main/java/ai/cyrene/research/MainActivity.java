package ai.cyrene.research;
import android.app.Activity;
import android.os.Bundle;
import android.util.Log;
import com.max2idea.android.limbo.jni.VMExecutor;
import java.io.File;
import java.util.ArrayList;
import java.util.Arrays;

public class MainActivity extends Activity {
 @Override public void onCreate(Bundle b) {
  super.onCreate(b);
  final boolean restore=getIntent().getBooleanExtra("restore",false);
  final String snapshot=getIntent().getStringExtra("snapshot");
  new Thread(() -> {
   try {
    String dir=getFilesDir().getAbsolutePath();
    new File(dir+"/etc").mkdirs();
    try(java.io.FileWriter w=new java.io.FileWriter(dir+"/etc/resolv.conf")){w.write("nameserver 10.0.2.3\n");}
    String lib="libqemu-system-aarch64.so";
    ArrayList<String> args=new ArrayList<>(Arrays.asList(lib,
     "-machine","virt","-cpu","cortex-a72","-smp","4","-accel","tcg,thread=multi","-m","4096",
     "-kernel",dir+"/vmlinuz-virt","-initrd",dir+"/initramfs-cyrene",
     "-append","console=ttyAMA0 root=/dev/vda rw net.ifnames=0 panic=-1 loglevel=4",
     "-nodefaults","-display","none","-monitor","none",
     "-qmp","tcp:127.0.0.1:4544,server=on,wait=off",
     "-chardev","socket,id=cyrene,host=127.0.0.1,port=4545,server=on,wait=off",
     "-serial","chardev:cyrene",
     "-drive","if=none,id=rootfs,file="+dir+"/rootfs.qcow2,format=qcow2,cache=writeback",
     "-device","virtio-blk-pci,drive=rootfs",
     "-netdev","user,id=net0,restrict=off,hostfwd=tcp:127.0.0.1:4546-:4243",
     "-device","virtio-net-pci,netdev=net0",
     "-object","rng-builtin,id=cyrene-rng","-device","virtio-rng-pci,rng=cyrene-rng",
     "-no-reboot","-overcommit","mem-lock=off","-L",dir));
    if(restore){ args.add("-loadvm"); args.add(snapshot==null?"ready":snapshot); }
    Log.i("SnapshotProbe","START restore="+restore+" time="+android.os.SystemClock.elapsedRealtime());
    String result=new VMExecutor().start(dir,dir,lib,new File(getApplicationInfo().nativeLibraryDir,lib).getAbsolutePath(),0,args.toArray(new String[0]));
    Log.e("SnapshotProbe","EXIT "+result);
   }catch(Throwable t){Log.e("SnapshotProbe","FAILED",t);}
  },"qemu").start();
 }
}

"""Package an internal macOS pilot without requiring a Developer ID identity."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from build_macos import clone_copy


def main():
    p=argparse.ArgumentParser()
    p.add_argument('app',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    app=args.app.resolve(); output=args.output.resolve()
    if output.exists(): p.error('choose a new output directory')
    output.mkdir(parents=True)
    # Ad-hoc integrity signing is not Developer ID signing or notarization.
    subprocess.run(['/usr/bin/codesign','--force','--deep','--sign','-',str(app)],check=True)
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(app)],check=True)
    with tempfile.TemporaryDirectory(prefix='carry-pilot-') as temp:
        folder=Path(temp)/'Carry Pilot'; folder.mkdir()
        shutil.copytree(app,folder/'Carry.app',symlinks=True,copy_function=clone_copy)
        guide=Path(__file__).resolve().parents[1]/'docs/PILOT.md'
        shutil.copy2(guide,folder/'README.md')
        (folder/'Kurulum.txt').write_text('''Carry — şirket içi pilot (Apple silicon, macOS 14+)\n\n1. Carry.app dosyasını Applications klasörüne taşıyın; bağlantıları bundan sonra kurun.\n2. İlk açılış engellenirse Sistem Ayarları > Gizlilik ve Güvenlik bölümündeki uygulamaya özel Yine de Aç seçeneğini kullanın. Kurumunuz yönetilen Mac uygulamalarını kısıtlıyorsa BT ekibine başvurun.\n3. Carry içinde bir workspace oluşturun. Sources > Sign in to GitHub ile giriş yapın ve repoyu seçin. Connect and index düğmesine basın.\n4. Accurate multilingual search > Download and enable model ile Türkçe/İngilizce aramayı kurun. Yaklaşık 3 GB model indirilir.\n5. Connections ekranında asistanı ve proje klasörünü seçin; Preview > Apply adımlarından sonra asistanı yeniden başlatın. Yalnız bilgi aramak için capture seçeneklerini açmanız gerekmez.\n6. Search ekranında sorular deneyin ve kaynakları açarak kontrol edin.\n\nBu paket Developer ID ile imzalı/notarize değildir. GitHub'a yazmaz. Arama kusursuz değildir; kaynakların soruyu gerçekten yanıtladığını kontrol edin. Ayrıntılar README.md içinde.\n''')
        zipfile=output/'Carry-Internal-Pilot-arm64.zip'
        subprocess.run(['/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',str(folder),str(zipfile)],check=True)
        shutil.copytree(app,output/'Carry.app',symlinks=True,copy_function=clone_copy)
        shutil.copy2(folder/'Kurulum.txt',output/'Kurulum.txt')
        shutil.copy2(guide,output/'README.md')
    sha=hashlib.sha256(zipfile.read_bytes()).hexdigest()
    (output/'SHA256.txt').write_text(sha+'  '+zipfile.name+'\n')
    report=dict(file=zipfile.name,bytes=zipfile.stat().st_size,sha256=sha,
                developer_id_signed=False,notarized=False,ad_hoc_integrity_verified=True)
    (output/'package.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))

if __name__=='__main__':main()

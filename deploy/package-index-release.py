"""Package an already built manual-index release without copying local environment files.

Build the image, platform dist and data-biz JAR first. This script does not deploy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def git(repo, *args):
    return subprocess.check_output(['git', '-c', 'core.quotepath=false', *args], cwd=repo)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--platform-backend', type=Path, required=True)
    parser.add_argument('--platform-frontend', type=Path, required=True)
    parser.add_argument('--dist', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--image', default='sqlbot-cx-backend-host:index-20260908')
    parser.add_argument('--existing-image-tar', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    jar = args.platform_backend / 'yudao-module-data/yudao-module-data-biz/target/yudao-module-data-biz.jar'
    if not jar.is_file() or not (args.dist / 'index.html').is_file():
        raise RuntimeError('Build data-biz JAR and platform frontend before packaging')
    image_info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.image]))[0]
    tar = output / 'sqlbot-backend-index-20260908.tar'
    if args.existing_image_tar:
        if args.existing_image_tar.resolve() != tar:
            shutil.copy2(args.existing_image_tar, tar)
    else:
        subprocess.run(['docker', 'save', '-o', str(tar), args.image], check=True)
    if not tar.is_file() or tar.stat().st_size < 1024:
        raise RuntimeError('Image export is missing')
    shutil.copy2(jar, output / jar.name)
    with zipfile.ZipFile(output / 'platform-frontend.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(args.dist.rglob('*')):
            if file.is_file():
                archive.write(file, file.relative_to(args.dist).as_posix())
    scopes = {
        'SQLBot-cx': (root, ['backend/alembic', 'backend/apps', 'backend/main.py', 'backend/scripts/benchmark_platform_index.py',
                            'backend/scripts/verify_phase2a_3a.py', 'backend/tests/platform_index',
                            'deploy/Dockerfile.backend-host', 'deploy/package-index-release.py']),
        'bcback-sqlbot': (args.platform_backend, ['yudao-module-data/yudao-module-data-biz/src/main/java',
                                                'yudao-module-data/yudao-module-data-biz/src/test/java']),
        'academy8-operation-control-center-frontend': (args.platform_frontend,
            ['src/api/dataResource/tableEmbeddings.ts', 'src/views/dataresource/datasource/index.vue',
             'src/views/dataresource/datasource/IndexSyncButton.vue'])
    }
    manifest = dict(image=args.image, imageId=image_info['Id'], repoDigests=image_info.get('RepoDigests', []),
                    platform=image_info['Os'] + '/' + image_info['Architecture'], repositories={},
                    deploymentVerified=False, businessRecallValidated=False)
    with zipfile.ZipFile(output / 'source-overlay.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, (repo, paths) in scopes.items():
            changes = git(repo, 'diff', '--name-only', '--', *paths).decode().splitlines()
            changes += git(repo, 'ls-files', '--others', '--exclude-standard', '--', *paths).decode().splitlines()
            files = {rel: sha256(repo / rel) for rel in sorted(set(changes)) if (repo / rel).is_file()}
            manifest['repositories'][name] = dict(head=git(repo, 'rev-parse', 'HEAD').decode().strip(),
                                                  committed=False, overlaySha256=files)
            for rel in files:
                archive.write(repo / rel, name + '/' + rel)
            archive.writestr(name + '.patch', git(repo, 'diff', '--binary', '--', *paths))
    for name in ['问数索引手动同步.md', '问数索引验证报告-20260908.md']:
        shutil.copy2(root / 'docs/integration' / name, output / name)
    evidence = output / 'evidence'
    evidence.mkdir(exist_ok=True)
    for name in ['benchmark-100.json', 'benchmark-300.json', 'benchmark-concurrent.json', 'benchmark_concurrent.py', 'python-tests.log', 'auth-regression.md',
                 'permission-regression.md', 'image-smoke.log', 'frontend-build.log', 'java-test-results.txt']:
        source = args.evidence / name
        if source.is_file():
            shutil.copy2(source, evidence / name)
    (output / 'release-manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    checksums = []
    for file in sorted(output.rglob('*')):
        if file.is_file() and file.name != 'SHA256SUMS':
            checksums.append(sha256(file) + '  ' + file.relative_to(output).as_posix())
    (output / 'SHA256SUMS').write_text('\n'.join(checksums) + '\n', encoding='utf-8')
    print('OFFLINE_PACKAGE_OK', output)


if __name__ == '__main__':
    main()

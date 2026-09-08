// Build a stored ZIP locally so page images never need an upload.
document.addEventListener('DOMContentLoaded', () => {
    const picker = document.getElementById('local-pages');
    const button = document.getElementById('local-cbz');
    const status = document.getElementById('local-cbz-status');
    const table = Array.from({length: 256}, (_, index) => {
        let value = index;
        for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0);
        return value >>> 0;
    });
    const crc32 = bytes => {
        let crc = 0xffffffff;
        for (const byte of bytes) crc = (crc >>> 8) ^ table[(crc ^ byte) & 255];
        return (crc ^ 0xffffffff) >>> 0;
    };
    picker.addEventListener('change', () => {
        button.disabled = !picker.files.length;
        status.textContent = `${picker.files.length} sayfa seçildi. Dosya adına göre sıralanacak.`;
    });
    button.addEventListener('click', async () => {
        const files = [...picker.files].sort((a, b) => a.name.localeCompare(b.name, 'tr', {numeric: true}));
        if (!files.length) return;
        if (files.length > 500 || files.reduce((sum, file) => sum + file.size, 0) > 200 * 1024 * 1024) {
            status.textContent = 'Bir paket en fazla 500 sayfa ve 200 MB olabilir.';
            return;
        }
        button.disabled = true;
        try {
            const chunks = [], directory = [];
            let offset = 0;
            for (let index = 0; index < files.length; index++) {
                const file = files[index];
                if (!/\.(jpe?g|png|webp)$/i.test(file.name)) throw new Error('Yalnız JPG, PNG ve WebP seçin.');
                const preview = await createImageBitmap(file);
                preview.close();
                const bytes = new Uint8Array(await file.arrayBuffer());
                const name = new TextEncoder().encode(`${String(index + 1).padStart(4, '0')}.${file.name.split('.').pop().toLowerCase()}`);
                const crc = crc32(bytes);
                const header = new Uint8Array(30 + name.length);
                const view = new DataView(header.buffer);
                view.setUint32(0, 0x04034b50, true);
                view.setUint16(4, 20, true);
                view.setUint32(14, crc, true);
                view.setUint32(18, bytes.length, true);
                view.setUint32(22, bytes.length, true);
                view.setUint16(26, name.length, true);
                header.set(name, 30);
                const central = new Uint8Array(46 + name.length);
                const entry = new DataView(central.buffer);
                entry.setUint32(0, 0x02014b50, true);
                entry.setUint16(4, 20, true);
                entry.setUint16(6, 20, true);
                entry.setUint32(16, crc, true);
                entry.setUint32(20, bytes.length, true);
                entry.setUint32(24, bytes.length, true);
                entry.setUint16(28, name.length, true);
                entry.setUint32(42, offset, true);
                central.set(name, 46);
                chunks.push(header, bytes);
                directory.push(central);
                offset += header.length + bytes.length;
                status.textContent = `${index + 1} / ${files.length} sayfa hazırlandı.`;
            }
            const end = new Uint8Array(22);
            const view = new DataView(end.buffer);
            view.setUint32(0, 0x06054b50, true);
            view.setUint16(8, files.length, true);
            view.setUint16(10, files.length, true);
            view.setUint32(12, directory.reduce((sum, entry) => sum + entry.length, 0), true);
            view.setUint32(16, offset, true);
            const url = URL.createObjectURL(new Blob([...chunks, ...directory, end], {type: 'application/vnd.comicbook+zip'}));
            const link = document.createElement('a');
            link.href = url;
            link.download = 'manga.cbz';
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 60000);
            status.textContent = `${files.length} sayfalık CBZ hazır. İndirme başlatıldı.`;
        } catch (error) {
            status.textContent = `Paket oluşturulamadı: ${error.message}`;
        } finally {
            button.disabled = false;
        }
    });
});

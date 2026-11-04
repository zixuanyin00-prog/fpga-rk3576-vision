
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/dma-mapping.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/mm.h>
#include <linux/uaccess.h>
#include <linux/mutex.h>
#include <linux/delay.h>
#include <linux/ktime.h>
#include <linux/kref.h>
#include <linux/version.h>
#include "camera_dma_uapi.h"

struct cam_device {
    struct pci_dev *pdev;
    void __iomem *regs;
    void *pool[CAM_CHUNK_COUNT];
    dma_addr_t dma[CAM_CHUNK_COUNT];
    struct miscdevice misc;
    struct mutex lock;
    struct kref refs;
    bool opened, running, quarantined, removed;
};

static void *cam_pool_ptr(struct cam_device *c, u32 offset) {
    return (u8 *)c->pool[offset >> CAM_CHUNK_SHIFT] + (offset & (CAM_CHUNK_BYTES - 1u));
}
static void cam_free_chunks(struct cam_device *c) {
    unsigned int i;
    for (i = 0; i < CAM_CHUNK_COUNT; i++)
        if (c->pool[i]) {
            dma_free_coherent(&c->pdev->dev, CAM_CHUNK_BYTES, c->pool[i], c->dma[i]);
            c->pool[i] = NULL;
        }
}
static void cam_destroy(struct kref *refs) {
    struct cam_device *c = container_of(refs, struct cam_device, refs);
    if (c->quarantined)
        return;
    cam_free_chunks(c);
    pci_iounmap(c->pdev, c->regs);
    pci_dev_put(c->pdev);
    kfree(c);
}
static u32 cam_read(struct cam_device *c, u32 offset) {
    return readl(c->regs + offset);
}
static void cam_write(struct cam_device *c, u32 offset, u32 value) {
    writel(value, c->regs + offset);
    (void)cam_read(c, offset);
}
static int cam_process_valid(const u32 *v) {
    u32 mode = (v[2] >> 16) & 3u;
    if (v[0] > 6 || (v[1] & ~CAM_PROCESS_SUPPORTED) || (v[2] & ~0x7ffffu) || mode == 3 ||
        (mode == 2 && (v[2] & 255u) > ((v[2] >> 8) & 255u)) || (v[3] & ~0x37u) || (v[3] & 7u) > 4 ||
        (v[4] & 0xfffe0000u) || (v[4] & 0xfffu) > 2040 || ((v[4] >> 12) & 15u) > 8 ||
        (v[5] & 0xc0000000u) || ((v[5] >> 24) & 7u) > 4 || ((v[5] >> 27) & 7u) > 4 ||
        (v[6] & ~0x00ff0717u) || (v[6] & 7u) > 4 || ((v[6] >> 8) & 7u) > 4 ||
        (v[7] & 0xffff0000u) || v[8] > 256 || v[9] > 256)
        return -EINVAL;
    if ((v[0] == 2 || v[0] == 3) && !(v[1] & (1u << 2)))
        return -EINVAL;
    if (v[0] == 4 && !(v[1] & (1u << 4)))
        return -EINVAL;
    if (v[0] == 5 && (!(v[1] & (1u << 9)) || !(v[6] & 7u)))
        return -EINVAL;
    if (v[0] == 6 && ((v[1] & ((1u << 2) | (1u << 11))) != ((1u << 2) | (1u << 11))))
        return -EINVAL;
    return 0;
}
static int cam_process_read(struct cam_device *c, struct cam_process_status *s) {
    unsigned i;
    u32 requested, effective;
    memset(s, 0, sizeof(*s));
    s->magic = cam_read(c, 0x300);
    s->abi = cam_read(c, 0x304);
    s->supported_mask = cam_read(c, 0x308);
    s->word_count = cam_read(c, 0x30c);
    if (s->magic != CAM_PROCESS_MAGIC || s->abi != CAM_PROCESS_ABI ||
        s->word_count != CAM_PROCESS_WORDS)
        return -EOPNOTSUPP;
    requested = s->requested_revision = cam_read(c, 0x31c);
    effective = s->effective_revision = cam_read(c, 0x320);
    s->effective_source = cam_read(c, 0x324);
    s->status = cam_read(c, 0x330);
    s->rejected = cam_read(c, 0x334);
    for (i = 0; i < CAM_PROCESS_WORDS; i++) {
        cam_write(c, 0x310, i);
        s->requested_words[i] = cam_read(c, 0x32c);
        s->effective_words[i] = cam_read(c, 0x328);
    }
    if (requested != cam_read(c, 0x31c) || effective != cam_read(c, 0x320))
        return -EAGAIN;
    return 0;
}
static int cam_process_commit(struct cam_device *c, struct cam_process_config *p) {
    unsigned i;
    u32 previous = cam_read(c, 0x31c), effective = cam_read(c, 0x320), revision;
    int ret = cam_process_valid(p->words);
    if (ret)
        return ret;
    if (cam_read(c, 0x300) != CAM_PROCESS_MAGIC)
        return -EOPNOTSUPP;
    if (previous != effective)
        return -EBUSY;
    revision = p->revision ? p->revision : previous + 1;
    if (!revision)
        revision = 1;
    if (revision == previous)
        return -EALREADY;
    for (i = 0; i < CAM_PROCESS_WORDS; i++) {
        cam_write(c, 0x310, i);
        cam_write(c, 0x314, p->words[i]);
    }
    cam_write(c, 0x318, revision);
    if (cam_read(c, 0x31c) != revision)
        return -EIO;
    p->revision = revision;
    return 0;
}
static int cam_process_strength(struct cam_device *c, unsigned index, u32 value) {
    struct cam_process_status state;
    struct cam_process_config p;
    int ret = cam_process_read(c, &state);
    if (ret)
        return ret;
    if (state.requested_words[index] == value)
        return 0;
    memcpy(p.words, state.requested_words, sizeof(p.words));
    p.words[index] = value;
    p.revision = 0;
    return cam_process_commit(c, &p);
}
static int cam_stop(struct cam_device *c) {
    u32 *marker = cam_pool_ptr(c, CAM_STOP_OFFSET);
    u32 *infer_marker = cam_pool_ptr(c, CAM_ROI_BASE + CAM_SLOT_STRIDE - 16u);
    unsigned int attempt;
    bool dual = cam_read(c, 0xfc) == 0;
    if (!c->running)
        return 0;
    cam_write(c, 0x0c, 0);

    for (attempt = 0; attempt < 2000; attempt++) {
        bool preview_done = true;
        u32 *preview_marker = cam_pool_ptr(c, CAM_RAW_PREVIEW_STOP);
        if (READ_ONCE(preview_marker[0]) != CAM_STOP_MAGIC ||
            READ_ONCE(preview_marker[3]) != CAM_STOP_MAGIC)
            preview_done = false;
        if (READ_ONCE(marker[0]) == CAM_STOP_MAGIC && READ_ONCE(marker[3]) == CAM_STOP_MAGIC &&
            READ_ONCE(infer_marker[0]) == CAM_STOP_MAGIC &&
            READ_ONCE(infer_marker[3]) == CAM_STOP_MAGIC && (!dual || preview_done)) {
            dma_rmb();
            pci_clear_master(c->pdev);
            c->running = false;
            return 0;
        }
        usleep_range(500, 1000);
    }
    pci_clear_master(c->pdev);
    c->quarantined = true;
    dev_err(&c->pdev->dev, "DMA STOP fence timed out; retain coherent allocation\n");
    return -ETIMEDOUT;
}
static int cam_open(struct inode *inode, struct file *file) {
    struct miscdevice *misc = file->private_data;
    struct cam_device *c = container_of(misc, struct cam_device, misc);
    int ret = 0;
    (void)inode;
    mutex_lock(&c->lock);
    if (c->removed)
        ret = -ENODEV;
    else if (c->opened || c->quarantined)
        ret = -EBUSY;
    else {
        c->opened = true;
        kref_get(&c->refs);
        file->private_data = c;
    }
    mutex_unlock(&c->lock);
    return ret ? ret : nonseekable_open(inode, file);
}
static int cam_release(struct inode *inode, struct file *file) {
    struct cam_device *c = file->private_data;
    int ret;
    (void)inode;
    mutex_lock(&c->lock);
    ret = c->removed ? 0 : cam_stop(c);
    c->opened = false;
    mutex_unlock(&c->lock);
    kref_put(&c->refs, cam_destroy);
    return ret;
}
static long cam_ioctl(struct file *file, unsigned int command, unsigned long arg) {
    struct cam_device *c = file->private_data;
    int ret = 0;
    struct cam_wb wb;
    struct cam_info info = {CAM_VERSION,     1920,           1080,
                            CAM_FRAME_BYTES, CAM_SLOT_COUNT, CAM_SLOT_STRIDE,
                            CAM_META_OFFSET, CAM_POOL_BYTES};
    struct cam_isp isp;
    struct cam_rect rect;
    struct cam_roi roi;
    u32 mode, ctrl_reg, xy_reg, id_reg, wh_reg, commit_reg, ready_reg, epoch;
    struct cam_filters filters;
    struct cam_status status;
    struct cam_clock clock;
    unsigned int fi;
    u32 industrial_config, industrial_frame;
    struct cam_processing processing;
    struct cam_process_config process_config;
    struct cam_sensor_request sensor_request;
    u32 quality_origin;
    struct cam_enhance enhance;
    union {
        struct cam_features features;
        struct cam_industrial industrial;
        struct cam_process_status process_status;
        struct cam_sensor_status sensor_status;
        struct cam_quality quality;
        struct cam_enhance_status enhance_status;
    } reply;
    unsigned int wait;
    mutex_lock(&c->lock);
    if (c->removed) {
        ret = -ENODEV;
        goto out;
    }
    if (c->quarantined) {
        ret = -EIO;
        goto out;
    }
    info.width = cam_read(c, 0x64);
    info.height = cam_read(c, 0x68);
    info.version = cam_read(c, 4);
    info.frame_bytes = cam_read(c, 0xfc) ? 491520u : CAM_FRAME_BYTES;
    switch (command) {
    case CAM_PROCESS_STATUS:
        ret = cam_process_read(c, &reply.process_status);
        if (!ret &&
            copy_to_user((void __user *)arg, &reply.process_status, sizeof(reply.process_status)))
            ret = -EFAULT;
        break;
    case CAM_PROCESS_SUBMIT:
        if (copy_from_user(&process_config, (void __user *)arg, sizeof(process_config))) {
            ret = -EFAULT;
            break;
        }
        ret = cam_process_commit(c, &process_config);
        if (!ret && copy_to_user((void __user *)arg, &process_config, sizeof(process_config)))
            ret = -EFAULT;
        break;
    case CAM_GET_PROCESSING:
        if (info.version != CAM_VERSION || cam_read(c, CAM_STREAM_MAGIC_CSR) != CAM_STREAM_MAGIC) {
            ret = -EOPNOTSUPP;
            break;
        }
        processing.epoch = cam_read(c, CAM_EPOCH_CSR);
        processing.revision = cam_read(c, 0x24c);
        processing.industrial = cam_read(c, 0x9c);
        processing.features = cam_read(c, 0x90);
        if (copy_to_user((void __user *)arg, &processing, sizeof(processing)))
            ret = -EFAULT;
        break;
    case CAM_ENHANCE_SUBMIT:
        if (info.version != CAM_VERSION || cam_read(c, 0xfc) ||
            cam_read(c, 0x1a0) != CAM_ENHANCE_MAGIC || cam_read(c, 0x1a4) != 1) {
            ret = -EOPNOTSUPP;
            break;
        }
        if (copy_from_user(&enhance, (void __user *)arg, sizeof(enhance))) {
            ret = -EFAULT;
            break;
        }
        if (enhance.gain_q8 < 64 || enhance.gain_q8 > 1024 || enhance.strength_q8 > 256 ||
            !enhance.revision || enhance.knots[8] > 256) {
            ret = -EINVAL;
            break;
        }
        for (fi = 0; fi < 8; fi++)
            if (enhance.knots[fi] > enhance.knots[fi + 1]) {
                ret = -EINVAL;
                break;
            }
        if (ret)
            break;
        if (cam_read(c, 0x1d4) == enhance.revision) {
            ret = -EALREADY;
            break;
        }

        if (cam_read(c, 0x1d4) != cam_read(c, 0x1dc)) {
            ret = -EBUSY;
            break;
        }
        ret = cam_process_read(c, &reply.process_status);
        if (ret)
            break;
        if (reply.process_status.requested_words[9] != enhance.strength_q8 &&
            reply.process_status.requested_revision != reply.process_status.effective_revision) {
            ret = -EBUSY;
            break;
        }
        cam_write(c, 0x1a8, enhance.gain_q8);
        cam_write(c, 0x1ac, enhance.strength_q8);
        for (fi = 0; fi < 9; fi++)
            cam_write(c, 0x1b0 + fi * 4, enhance.knots[fi]);
        cam_write(c, 0x1d4, enhance.revision);
        cam_write(c, 0x1d8, CAM_ENHANCE_MAGIC);
        if (cam_read(c, 0x1d4) != enhance.revision) {
            ret = -EIO;
            break;
        }
        ret = cam_process_strength(c, 9, enhance.strength_q8);
        break;
    case CAM_ENHANCE_STATUS:
        if (info.version != CAM_VERSION || cam_read(c, 0x1a0) != CAM_ENHANCE_MAGIC) {
            ret = -EOPNOTSUPP;
            break;
        }
        reply.enhance_status.magic = CAM_ENHANCE_MAGIC;
        reply.enhance_status.abi = cam_read(c, 0x1a4);
        reply.enhance_status.active.gain_q8 = cam_read(c, 0x1a8);
        reply.enhance_status.active.strength_q8 = cam_read(c, 0x1ac);
        reply.enhance_status.active.revision = cam_read(c, 0x1d4);
        for (fi = 0; fi < 9; fi++)
            reply.enhance_status.active.knots[fi] = cam_read(c, 0x1b0 + fi * 4);
        reply.enhance_status.effective_revision = cam_read(c, 0x1dc);
        reply.enhance_status.source_frame = cam_read(c, 0x1e0);
        if (reply.enhance_status.active.revision != cam_read(c, 0x1d4) ||
            reply.enhance_status.effective_revision != cam_read(c, 0x1dc)) {
            ret = -EAGAIN;
            break;
        }
        if (copy_to_user((void __user *)arg, &reply.enhance_status, sizeof(reply.enhance_status)))
            ret = -EFAULT;
        break;
    case CAM_SENSOR_SUBMIT:
        if ((info.version != CAM_VERSION && info.version != CAM_HOTUPDATE_VERSION) ||
            cam_read(c, 0x100) != CAM_SENSOR_MAGIC || cam_read(c, 0x104) != 1) {
            ret = -EOPNOTSUPP;
            break;
        }
        if (copy_from_user(&sensor_request, (void __user *)arg, sizeof(sensor_request))) {
            ret = -EFAULT;
            break;
        }
        if (!sensor_request.request_id || sensor_request.shr0 < 8 || sensor_request.shr0 > 2279 ||
            sensor_request.gain_code > 100 || sensor_request.flags > CAM_SENSOR_RESTORE) {
            ret = -EINVAL;
            break;
        }
        if (cam_read(c, 0x11c)) {
            ret = -EBUSY;
            break;
        }
        if (!(cam_read(c, 0x18) & 4)) {
            ret = -EAGAIN;
            break;
        }
        if (cam_read(c, 0x108) == sensor_request.request_id) {
            ret = -EALREADY;
            break;
        }
        cam_write(c, 0x108, sensor_request.request_id);
        cam_write(c, 0x10c, sensor_request.shr0);
        cam_write(c, 0x110, sensor_request.gain_code);
        cam_write(c, 0x114, sensor_request.flags);
        cam_write(c, 0x118, CAM_SENSOR_MAGIC);
        if (cam_read(c, 0x108) != sensor_request.request_id) {
            ret = -EIO;
            break;
        }

        break;
    case CAM_SENSOR_STATUS:
        if ((info.version != CAM_VERSION && info.version != CAM_HOTUPDATE_VERSION) ||
            cam_read(c, 0x100) != CAM_SENSOR_MAGIC) {
            ret = -EOPNOTSUPP;
            break;
        }
        reply.sensor_status.magic = CAM_SENSOR_MAGIC;
        reply.sensor_status.abi = cam_read(c, 0x104);
        reply.sensor_status.busy = cam_read(c, 0x11c);
        reply.sensor_status.submitted_id = cam_read(c, 0x108);
        for (fi = 0; fi < 16; fi++)
            reply.sensor_status.result[fi] = cam_read(c, 0x120 + fi * 4);

        if (reply.sensor_status.busy != cam_read(c, 0x11c) ||
            reply.sensor_status.result[0] != cam_read(c, 0x120)) {
            ret = -EAGAIN;
            break;
        }
        if (copy_to_user((void __user *)arg, &reply.sensor_status, sizeof(reply.sensor_status)))
            ret = -EFAULT;
        break;
    case CAM_QUALITY_ORIGIN:
        if ((info.version != CAM_VERSION && info.version != CAM_HOTUPDATE_VERSION) ||
            cam_read(c, 0x100) != CAM_SENSOR_MAGIC) {
            ret = -EOPNOTSUPP;
            break;
        }
        if (copy_from_user(&quality_origin, (void __user *)arg, sizeof(quality_origin))) {
            ret = -EFAULT;
            break;
        }
        if ((quality_origin & 65535) > 1408 || (quality_origin >> 16) > 792) {
            ret = -EINVAL;
            break;
        }
        cam_write(c, 0x180, quality_origin);
        break;
    case CAM_QUALITY_SNAPSHOT:
        if ((info.version != CAM_VERSION && info.version != CAM_HOTUPDATE_VERSION) ||
            cam_read(c, 0x100) != CAM_SENSOR_MAGIC) {
            ret = -EOPNOTSUPP;
            break;
        }
        cam_write(c, 0x184, 1);
        for (fi = 0; fi < 24; fi++) {
            cam_write(c, 0x188, fi);
            reply.quality.values[fi] = cam_read(c, 0x18c);
        }
        cam_write(c, 0x184, 0);
        if (reply.quality.values[0] != 0x31545351 || !reply.quality.values[1]) {
            ret = -EAGAIN;
            break;
        }
        if (copy_to_user((void __user *)arg, &reply.quality, sizeof(reply.quality)))
            ret = -EFAULT;
        break;
    case CAM_GET_INDUSTRIAL:
        if (cam_read(c, 0xcc) != 0x31444e49) {
            ret = -EOPNOTSUPP;
            break;
        }
        cam_write(c, 0x74, 82);
        industrial_frame = cam_read(c, 0x78);
        if (!industrial_frame) {
            ret = -EAGAIN;
            break;
        }
        for (fi = 0; fi < 32; fi++) {
            cam_write(c, 0x74, 80 + fi);
            reply.industrial.values[fi] = cam_read(c, 0x78);
        }
        cam_write(c, 0x74, 82);
        if (industrial_frame != cam_read(c, 0x78) ||
            reply.industrial.values[2] != industrial_frame) {
            ret = -EAGAIN;
            break;
        }
        if (copy_to_user((void __user *)arg, &reply.industrial, sizeof(reply.industrial)))
            ret = -EFAULT;
        break;
    case CAM_GET_INDUSTRIAL_CONFIG:
        if (cam_read(c, 0xcc) != 0x31444e49) {
            ret = -EOPNOTSUPP;
            break;
        }
        industrial_config = cam_read(c, 0x9c);
        if (copy_to_user((void __user *)arg, &industrial_config, sizeof(industrial_config)))
            ret = -EFAULT;
        break;
    case CAM_SET_INDUSTRIAL:
        ret = -EOPNOTSUPP;
        break;
    case CAM_GET_FEATURES:
        if (cam_read(c, 0x98) != 0x34544653) {
            ret = -EOPNOTSUPP;
            break;
        }
        reply.features.frame = cam_read(c, 0x7c);
        if (!reply.features.frame) {
            ret = -EAGAIN;
            break;
        }
        for (fi = 0; fi < 80; fi++) {
            cam_write(c, 0x74, fi);
            reply.features.values[fi] = cam_read(c, 0x78);
        }
        reply.features.fault = cam_read(c, 0x94);
        if (reply.features.frame != cam_read(c, 0x7c)) {
            ret = -EAGAIN;
            break;
        }
        if (copy_to_user((void __user *)arg, &reply.features, sizeof(reply.features)))
            ret = -EFAULT;
        break;
    case CAM_SET_FEATURES:
        ret = -EOPNOTSUPP;
        break;
    case CAM_PREVIEW_COMPRESSION:
        if (copy_from_user(&mode, (void __user *)arg, sizeof(mode))) {
            ret = -EFAULT;
            break;
        }
        if (mode != CAM_PREVIEW_RAW) {
            ret = -EOPNOTSUPP;
            break;
        }
        if (cam_read(c, 0x1f4) != CAM_PREVIEW_RAW)
            ret = -EIO;
        break;
    case CAM_SET_MODE:
        if (copy_from_user(&mode, (void __user *)arg, sizeof(mode))) {
            ret = -EFAULT;
            break;
        }
        if (mode)
            ret = -EOPNOTSUPP;
        break;
    case CAM_SET_COMPRESSION:
        ret = -EOPNOTSUPP;
        break;
    case CAM_GET_INFO:
        if (copy_to_user((void __user *)arg, &info, sizeof(info)))
            ret = -EFAULT;
        break;
    case CAM_GET_CLOCK:
        clock.before_ns = ktime_get_ns();
        clock.tick = cam_read(c, 0x20);
        clock.after_ns = ktime_get_ns();
        clock.reserved = 0;
        if (copy_to_user((void __user *)arg, &clock, sizeof(clock)))
            ret = -EFAULT;
        break;
    case CAM_START:
        if (c->running) {
            ret = -EBUSY;
            break;
        }
        if (!(cam_read(c, 0x88) & 8)) {
            ret = -EBUSY;
            break;
        }

        for (wait = 0; wait < 3000; wait++) {
            if (cam_read(c, 0x18) & 4)
                break;
            usleep_range(800, 1200);
        }
        if (wait == 3000) {
            ret = -ETIMEDOUT;
            break;
        }

        cam_write(c, 0x0c, 0);
        if (cam_read(c, CAM_STREAM_MAGIC_CSR) != CAM_STREAM_MAGIC ||
            cam_read(c, CAM_MAX_ROI_CSR) != CAM_MAX_ROI_PIXELS) {
            ret = -EPROTO;
            break;
        }
        epoch = lower_32_bits(ktime_get_ns());
        if (!epoch)
            epoch = 1;
        cam_write(c, CAM_EPOCH_CSR, epoch);
        if (cam_read(c, CAM_EPOCH_CSR) != epoch) {
            ret = -EIO;
            break;
        }
        if (cam_read(c, CAM_SGM_MAGIC) != CAM_SGM_MAGIC_VALUE ||
            cam_read(c, CAM_SGM_CONFIG) != CAM_SGM_CONFIG_VALUE) {
            ret = -EPROTO;
            break;
        }
        for (fi = 0; fi < CAM_CHUNK_COUNT; fi++) {
            memset(c->pool[fi], 0, CAM_CHUNK_BYTES);
            cam_write(c, CAM_SGM_INDEX, fi);
            cam_write(c, CAM_SGM_BASE, lower_32_bits(c->dma[fi]));
            if (cam_read(c, CAM_SGM_INDEX) != fi ||
                cam_read(c, CAM_SGM_BASE) != lower_32_bits(c->dma[fi])) {
                ret = -EIO;
                break;
            }
        }
        if (ret)
            break;
        if (cam_read(c, CAM_SGM_VALID) != CAM_SGM_ALL_VALID) {
            ret = -EPROTO;
            break;
        }
        dma_wmb();
        cam_write(c, 0x80, CAM_LOGICAL_BASE);
        cam_write(c, 0x84, 0);
        pci_set_master(c->pdev);
        c->running = true;
        cam_write(c, 0x0c, 1);
        if (cam_read(c, 0x0c) != 1) {

            ret = cam_stop(c);
            if (!ret)
                ret = -EIO;
        }
        break;
    case CAM_STREAM_RECT:
    case CAM_REQUEST_RECT:
        if (info.version != CAM_VERSION || cam_read(c, 0xfc) != 0 ||
            cam_read(c, 0x1e8) != CAM_RECT_MAGIC || cam_read(c, 0x1ec) != 3) {
            ret = -EOPNOTSUPP;
            break;
        }
        if (!c->running) {
            ret = -EINVAL;
            break;
        }
        if (copy_from_user(&rect, (void __user *)arg, sizeof(rect))) {
            ret = -EFAULT;
            break;
        }
        if (!rect.request_id || rect.channel < 1 || rect.channel > 2 || rect.x >= 1920 ||
            rect.y >= 1080 || !rect.width || !rect.height || rect.width > 1920 - rect.x ||
            rect.height > 1080 - rect.y || rect.width * rect.height > CAM_MAX_ROI_PIXELS) {
            ret = -EINVAL;
            break;
        }
        ctrl_reg = rect.channel == 1 ? 0xe4 : 0x220;
        xy_reg = rect.channel == 1 ? 0xe8 : 0x224;
        id_reg = rect.channel == 1 ? 0xec : 0x228;
        wh_reg = rect.channel == 1 ? 0x1e4 : 0x22c;
        commit_reg = rect.channel == 1 ? 0xf0 : 0x230;
        ready_reg = rect.channel == 1 ? 0xf4 : 0x234;
        if (!(cam_read(c, ready_reg) & 1)) {
            ret = -EBUSY;
            break;
        }
        cam_write(c, wh_reg, rect.width | (rect.height << 16));
        cam_write(c, ctrl_reg, command == CAM_STREAM_RECT && rect.channel == 1 ? 3 : 1);
        cam_write(c, xy_reg, rect.x | (rect.y << 16));
        cam_write(c, id_reg, rect.request_id);
        cam_write(c, commit_reg, 0x524f4931);
        if (cam_read(c, id_reg) != rect.request_id ||
            cam_read(c, xy_reg) != (rect.x | (rect.y << 16)) ||
            cam_read(c, wh_reg) != (rect.width | (rect.height << 16)))
            ret = -EIO;
        break;
    case CAM_REQUEST_ROI:
        if (cam_read(c, 0xfc) != 0) {
            ret = -EOPNOTSUPP;
            break;
        }
        if (!c->running) {
            ret = -EINVAL;
            break;
        }
        if (copy_from_user(&roi, (void __user *)arg, sizeof(roi))) {
            ret = -EFAULT;
            break;
        }
        if (!roi.request_id || roi.roi != 1 || roi.x > 1408 || roi.y > 792 ||
            (!roi.roi && (roi.x || roi.y))) {
            ret = -EINVAL;
            break;
        }
        if (!(cam_read(c, 0xf4) & 1)) {
            ret = -EBUSY;
            break;
        }
        cam_write(c, 0x1e4, 512 | (288 << 16));
        cam_write(c, 0xe4, roi.roi);
        cam_write(c, 0xe8, roi.x | (roi.y << 16));
        cam_write(c, 0xec, roi.request_id);
        cam_write(c, 0xf0, 0x524f4931);
        if (cam_read(c, 0xec) != roi.request_id || cam_read(c, 0xe4) != roi.roi ||
            cam_read(c, 0xe8) != (roi.x | (roi.y << 16)))
            ret = -EIO;
        break;
    case CAM_SET_FILTERS:
        if (copy_from_user(&filters, (void __user *)arg, sizeof(filters))) {
            ret = -EFAULT;
            break;
        }
        if (filters.gaussian || filters.equalize) {
            ret = -EOPNOTSUPP;
            break;
        }
        cam_write(c, 0xb4, filters.gaussian | filters.equalize << 1);
        if (cam_read(c, 0xb4) != (filters.gaussian | filters.equalize << 1))
            ret = -EIO;
        break;
    case CAM_SET_ISP:
        if (copy_from_user(&isp, (void __user *)arg, sizeof(isp))) {
            ret = -EFAULT;
            break;
        }
        if (isp.rg < 128 || isp.rg > 512 || isp.bg < 128 || isp.bg > 512 || isp.contrast < 76 ||
            isp.contrast > 768 || (s32)isp.brightness < -100 || (s32)isp.brightness > 100 ||
            isp.saturation != 256 || isp.gaussian > 256 || isp.he != 0 || isp.revision > 65535) {
            ret = -EINVAL;
            break;
        }
        ret = cam_process_read(c, &reply.process_status);
        if (ret)
            break;
        if (reply.process_status.requested_words[8] != isp.gaussian &&
            reply.process_status.requested_revision != reply.process_status.effective_revision) {
            ret = -EBUSY;
            break;
        }
        cam_write(c, 0xd0, isp.rg | (isp.bg << 16));
        cam_write(c, 0xd4, isp.contrast | ((isp.brightness & 65535) << 16));
        cam_write(c, 0xd8, isp.saturation | (isp.gaussian << 16));
        cam_write(c, 0xdc, isp.he | (isp.revision << 16));
        cam_write(c, 0xe0, 0x49535043);
        ret = cam_process_strength(c, 8, isp.gaussian);
        break;
    case CAM_GET_STATUS:
        status.status = cam_read(c, 0x88);
        status.last_frame = cam_read(c, 0x8c);
        status.bad_config = cam_read(c, 0xb0);
        status.protocol = cam_read(c, 0xbc);
        if (copy_to_user((void __user *)arg, &status, sizeof(status)))
            ret = -EFAULT;
        break;
    case CAM_STOP:
        ret = cam_stop(c);
        break;
    case CAM_SET_WB:
        if (copy_from_user(&wb, (void __user *)arg, sizeof(wb))) {
            ret = -EFAULT;
            break;
        }
        if (wb.manual > 1 || wb.r_q8 < 128 || wb.r_q8 > 512 || wb.b_q8 < 128 || wb.b_q8 > 512) {
            ret = -EINVAL;
            break;
        }
        cam_write(c, 0xa0, wb.manual);
        cam_write(c, 0xa4, wb.r_q8);
        cam_write(c, 0xa8, wb.b_q8);
        cam_write(c, 0xac, 0x5742434d);
        if (cam_read(c, 0xac) != (wb.manual << 20 | wb.b_q8 << 10 | wb.r_q8))
            ret = -EIO;
        break;
    default:
        ret = -ENOTTY;
    }
out:
    mutex_unlock(&c->lock);
    return ret;
}
static int cam_mmap(struct file *file, struct vm_area_struct *vma) {
    struct cam_device *c = file->private_data;
    unsigned long chunk_pages = CAM_CHUNK_BYTES >> PAGE_SHIFT;
    unsigned long index = vma->vm_pgoff / chunk_pages;
    if (vma->vm_pgoff % chunk_pages || index >= CAM_CHUNK_COUNT ||
        vma->vm_end - vma->vm_start != CAM_CHUNK_BYTES || vma->vm_flags & VM_WRITE)
        return -EINVAL;

    vma->vm_pgoff = 0;
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 3, 0)
    vm_flags_clear(vma, VM_MAYWRITE);
#else
    vma->vm_flags &= ~VM_MAYWRITE;
#endif
    return dma_mmap_coherent(&c->pdev->dev, vma, c->pool[index], c->dma[index], CAM_CHUNK_BYTES);
}
static const struct file_operations cam_fops = {.owner = THIS_MODULE,
                                                .open = cam_open,
                                                .release = cam_release,
                                                .unlocked_ioctl = cam_ioctl,
                                                .compat_ioctl = cam_ioctl,
                                                .mmap = cam_mmap};
static int cam_probe(struct pci_dev *pdev, const struct pci_device_id *id) {
    struct cam_device *c;
    int ret;
    unsigned int i;
    (void)id;
    ret = pci_enable_device_mem(pdev);
    if (ret)
        return ret;
    ret = pci_request_region(pdev, 0, "pcie_camera_dma");
    if (ret)
        goto disable;
    c = kzalloc(sizeof(*c), GFP_KERNEL);
    if (!c) {
        ret = -ENOMEM;
        goto region;
    }
    c->pdev = pci_dev_get(pdev);
    mutex_init(&c->lock);
    kref_init(&c->refs);
    c->regs = pci_iomap(pdev, 0, 0x1000);
    if (!c->regs) {
        ret = -ENOMEM;
        goto object;
    }
    if (cam_read(c, 0) != 0x50434531 || (cam_read(c, 4) != CAM_VERSION) ||
        cam_read(c, 0xc0) != 0x33534956) {
        ret = -ENODEV;
        goto unmap;
    }
    ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
    if (ret)
        goto unmap;
    pci_clear_master(pdev);
    cam_write(c, 0x0c, 0);
    if (cam_read(c, CAM_SGM_MAGIC) != CAM_SGM_MAGIC_VALUE ||
        cam_read(c, CAM_SGM_CONFIG) != CAM_SGM_CONFIG_VALUE) {
        ret = -EPROTO;
        goto unmap;
    }
    BUILD_BUG_ON(CAM_POOL_BYTES != CAM_CHUNK_COUNT * CAM_CHUNK_BYTES);

    for (i = 0; i < CAM_CHUNK_COUNT; i++) {
        c->pool[i] = dma_alloc_coherent(&pdev->dev, CAM_CHUNK_BYTES, &c->dma[i], GFP_KERNEL);
        if (!c->pool[i]) {
            dev_err(&pdev->dev, "coherent chunk %u/%u allocation failed (%u bytes)\n", i,
                    CAM_CHUNK_COUNT, CAM_CHUNK_BYTES);
            ret = -ENOMEM;
            goto pool;
        }
        if (!c->dma[i] || (c->dma[i] & (CAM_CHUNK_BYTES - 1u)) ||
            c->dma[i] > (dma_addr_t)(0xffffffffu - CAM_CHUNK_BYTES + 1u)) {
            ret = -EINVAL;
            goto pool;
        }
    }
    c->misc.minor = MISC_DYNAMIC_MINOR;
    c->misc.name = "pcie_camera_vision";
    c->misc.fops = &cam_fops;
    c->misc.parent = &pdev->dev;
    c->misc.mode = 0600;
    ret = misc_register(&c->misc);
    if (ret)
        goto pool;
    pci_set_drvdata(pdev, c);
    dev_info(&pdev->dev, "camera coherent DMA ready, %u slots, %u bytes\n", CAM_SLOT_COUNT,
             CAM_POOL_BYTES);
    dev_info(&pdev->dev, "SGM1 physical storage: %u independent %u-byte chunks\n", CAM_CHUNK_COUNT,
             CAM_CHUNK_BYTES);
    return 0;
pool:
    cam_free_chunks(c);
unmap:
    pci_iounmap(pdev, c->regs);
object:
    pci_dev_put(c->pdev);
    kfree(c);
region:
    pci_release_region(pdev, 0);
disable:
    pci_disable_device(pdev);
    return ret;
}
static void cam_remove(struct pci_dev *pdev) {
    struct cam_device *c = pci_get_drvdata(pdev);
    misc_deregister(&c->misc);
    mutex_lock(&c->lock);
    (void)cam_stop(c);
    c->removed = true;
    mutex_unlock(&c->lock);
    pci_clear_master(pdev);
    pci_release_region(pdev, 0);
    pci_disable_device(pdev);

    kref_put(&c->refs, cam_destroy);
}
static const struct pci_device_id cam_ids[] = {{PCI_DEVICE(0x1edb, 0xdcba)}, {0}};
MODULE_DEVICE_TABLE(pci, cam_ids);
static struct pci_driver cam_driver = {
    .name = "pcie_camera_vision", .id_table = cam_ids, .probe = cam_probe, .remove = cam_remove};
module_pci_driver(cam_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("FPGA native1080 three-stream SGM1 DMA with independent ROI STOP fences");

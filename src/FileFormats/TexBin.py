from ..serialization.Serializable import Serializable


def fill(rw, var, size):
    sz = (size - len(var))
    rw.rw_bytestring(b'\x00' * sz, sz)


def align(size, alignment):
    return (alignment - (size % alignment)) % alignment


class Texture(Serializable):
    def __init__(self):
        super().__init__()
        self.context.endianness = ">"
        
        self.name = b""
        self.size = 0
        self.payload = b""
    
    def read_write(self, rw):
        self.name = rw.rw_bytestring(self.name, 0xFC)
        fill(rw, self.name, 0xFC)
        self.size = rw.rw_uint32(self.size, "<")
        self.payload = rw.rw_bytestring(self.payload, self.size + align(self.size, 0x40))


class TexBinBinary(Serializable):
    def __init__(self):
        super().__init__()
        self.context.endianness = ">"
        
        self.name = b""
        self.unknown_1 = 9  # Texture type?
        self.num_bins = 0
        self.tex_counts = []
        self.textures = []
    
    def read_write(self, rw, ft):
        self.name = rw.rw_bytestring(self.name, 0xFC)
        fill(rw, self.name, 0xFC)
        self.unknown_1 = rw.rw_uint32(self.unknown_1)
        
        if rw.mode() == "read":
            config = rw.rw_bytestring(None, 0x40).rstrip(b'\x00')
            num_bins, tex_counts, _ = config.split(b',\r\n')
           
            self.num_bins  = int(num_bins)
            self.tex_counts = [int(t) for t in tex_counts.split(b',')]
        else:
            config = str(self.num_bins).encode('ascii') + b",\r\n"
            config += b','.join([str(t) for t in self.tex_counts]).encode('ascii') + b",\r\n"
            
            rw.rw_bytestring(config)
            fill(rw, config, 0x40)

        self.rw_textures(rw, self.tex_counts[0])
        
        for i, c in enumerate(self.tex_counts[1:]):
            fp = ft.format(i+1)
            with type(rw)(fp) as rw2:
                self.rw_textures(rw2, c)
    
    def rw_textures(self, rw, tex_count):
        for i in range(tex_count):
            if rw.mode() == "read":
                tx = Texture()
                rw.rw_obj(tx)
                self.textures.append(tx)
            else:
                rw.rw_obj(self.textures[i])
        
        rw.rw_bytestring(None, 0x100)

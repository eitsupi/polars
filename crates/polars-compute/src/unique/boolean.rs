use arrow::array::{Array, BooleanArray};
use arrow::bitmap::BitmapBuilder;
use arrow::datatypes::ArrowDataType;

use super::{GenericUniqueKernel, RangedUniqueKernel};

#[derive(Default, Clone)]
pub struct BooleanUniqueKernelState {
    seen: u32,
}

impl BooleanUniqueKernelState {
    pub fn new() -> Self {
        Self::default()
    }
}

impl RangedUniqueKernel for BooleanUniqueKernelState {
    type Array = BooleanArray;

    fn has_seen_all(&self) -> bool {
        self.seen == 0b111
    }

    fn append(&mut self, array: &Self::Array) {
        if array.len() == 0 {
            return;
        }

        let null_count = array.null_count();
        self.seen |= u32::from(null_count > 0) << 2;
        let num_trues = if null_count > 0 {
            array
                .values()
                .num_intersections_with(array.validity().unwrap())
        } else {
            array.values().set_bits()
        };

        self.seen |= u32::from(num_trues != array.len() - null_count);
        self.seen |= u32::from(num_trues != 0) << 1;
    }

    fn append_state(&mut self, other: &Self) {
        self.seen |= other.seen;
    }

    fn finalize_unique(self) -> Self::Array {
        let mut values = BitmapBuilder::with_capacity(self.seen.count_ones() as usize);

        if self.seen & 0b001 != 0 {
            values.push(false);
        }
        if self.seen & 0b010 != 0 {
            values.push(true);
        }
        let validity = if self.seen & 0b100 != 0 {
            let mut validity = BitmapBuilder::with_capacity(values.len() + 1);
            validity.extend_constant(values.len(), true);
            validity.push(false);
            values.push(false);
            Some(validity.freeze())
        } else {
            None
        };

        let values = values.freeze();
        BooleanArray::new(ArrowDataType::Boolean, values, validity)
    }

    fn finalize_n_unique(&self) -> usize {
        self.seen.count_ones() as usize
    }

    fn finalize_n_unique_non_null(&self) -> usize {
        (self.seen & 0b011).count_ones() as usize
    }
}

impl GenericUniqueKernel for BooleanArray {
    fn unique(&self) -> Self {
        let mut state = BooleanUniqueKernelState::new();
        state.append(self);
        state.finalize_unique()
    }

    fn n_unique(&self) -> usize {
        let mut state = BooleanUniqueKernelState::new();
        state.append(self);
        state.finalize_n_unique()
    }

    fn n_unique_non_null(&self) -> usize {
        let mut state = BooleanUniqueKernelState::new();
        state.append(self);
        state.finalize_n_unique_non_null()
    }
}

#[cfg(test)]
mod tests {
    use arrow::array::{BooleanArray, MutableBooleanArray, boolean_array};
    use proptest::prelude::*;

    use super::*;

    #[test]
    fn test_boolean_distinct_count() {
        use arrow::bitmap::Bitmap;
        use arrow::datatypes::ArrowDataType;

        macro_rules! assert_bool_dc {
            ($values:expr, $validity:expr => $dc:expr) => {
                let validity: Option<Bitmap> =
                    <Option<Vec<bool>>>::map($validity, |v| Bitmap::from_iter(v));
                let arr =
                    BooleanArray::new(ArrowDataType::Boolean, Bitmap::from_iter($values), validity);
                assert_eq!(arr.n_unique(), $dc);
            };
        }

        assert_bool_dc!(vec![], None => 0);
        assert_bool_dc!(vec![], Some(vec![]) => 0);
        assert_bool_dc!(vec![true], None => 1);
        assert_bool_dc!(vec![true], Some(vec![true]) => 1);
        assert_bool_dc!(vec![true], Some(vec![false]) => 1);
        assert_bool_dc!(vec![true, false], None => 2);
        assert_bool_dc!(vec![true, false, false], None => 2);
        assert_bool_dc!(vec![true, false, false], Some(vec![true, true, false]) => 3);

        // Copied from https://github.com/pola-rs/polars/pull/16765#discussion_r1629426159
        assert_bool_dc!(vec![true, true, true, true, true], Some(vec![true, false, true, false, false]) => 2);
        assert_bool_dc!(vec![false, true, false, true, true], Some(vec![true, false, true, false, false]) => 2);
        assert_bool_dc!(vec![true, false, true, false, true, true], Some(vec![true, true, false, true, false, false]) => 3);
    }

    proptest! {
        #[test]
        fn test_proptest(array in boolean_array(0..100)) {
            let mut state = BooleanUniqueKernelState::new();
            state.append(&array);

            let mut has_none = false;
            let mut has_false = false;
            let mut has_true = false;
            for v in array.iter() {
                match v {
                    None => has_none |= true,
                    Some(false) => has_false |= true,
                    Some(true) => has_true |= true,
                }
            }

            let mut unique = MutableBooleanArray::new();
            if has_false {
                unique.push(Some(false));
            }
            if has_true {
                unique.push(Some(true));
            }
            if has_none {
                unique.push(None);
            }
            let unique = unique.freeze();

            assert_eq!(state.clone().finalize_unique(), unique);
            assert_eq!(state.clone().finalize_n_unique(), unique.len());
            assert_eq!(state.clone().finalize_n_unique_non_null(), unique.len() - usize::from(has_none));
        }
    }
}
